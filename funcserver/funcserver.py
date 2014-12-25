from gevent import monkey; monkey.patch_all()

import gc
import os
import sys
import json
import time
import code
import socket
import random
import logging
import msgpack
import cStringIO
import urlparse
import argparse
import resource
import traceback
import threading
from ast import literal_eval

import gevent
import requests
import statsd
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.iostream
from tornado.template import BaseLoader, Template
from tornado.web import StaticFileHandler, HTTPError

MSG_TYPE_CONSOLE = 0
MSG_TYPE_LOG = 1

MAX_LOG_FILE_SIZE = 100 * 1024 * 1024 # 100MB

# set the logging level of requests module to warning
# otherwise it swamps with too many logs
logging.getLogger('requests').setLevel(logging.WARNING)

class RPCCallException(Exception):
    pass

class BaseHandler(tornado.web.RequestHandler):
    def get_template_namespace(self):
        ns = super(BaseHandler, self).get_template_namespace()
        ns.update(sys.funcserver.define_template_namespace())
        return ns

class PyInterpreter(code.InteractiveInterpreter):
    def __init__(self, *args, **kwargs):
        code.InteractiveInterpreter.__init__(self, *args, **kwargs)
        self.output = []

    def write(self, data):
        self.output.append(data)

class WSConnection(tornado.websocket.WebSocketHandler):
    '''
    Websocket based communication channel between a
    client and the server.
    '''

    WRITE_BUFFER_THRESHOLD = 1 * 1024 * 1024 # 1MB

    def open(self):
        '''
        Called when client opens connection. Initialization
        is done here.
        '''

        self.id = id(self)
        self.funcserver = self.application.funcserver

        # register this connection with node
        self.state = self.funcserver.websocks[self.id] = {'id': self.id, 'sock': self}

    def on_message(self, msg):
        '''
        Called when client sends a message.

        Supports a python debugging console. This forms
        the "eval" part of a standard read-eval-print loop.

        Currently the only implementation of the python
        console is in the WebUI but the implementation
        of a terminal based console is planned.
        '''

        msg = json.loads(msg)

        interpreter = self.state.get('interpreter', None)
        if interpreter is None:
            interpreter = PyInterpreter(self.funcserver.define_python_namespace())
            self.state['interpreter'] = interpreter

        code = msg['code']
        msg_id = msg['id']

        stdout = sys.stdout
        try:
            sys.stdout = cStringIO.StringIO()
            interpreter.runsource(code)
            output = sys.stdout.getvalue() or interpreter.output
            if isinstance(output, list): output = ''.join(output)
            interpreter.output = []
        finally:
            sys.stdout = stdout

        msg = {'type': MSG_TYPE_CONSOLE, 'id': msg_id, 'data': output}
        self.send_message(msg)

    def on_close(self):
        '''
        Called when client closes this connection. Cleanup
        is done here.
        '''

        if self.id in self.funcserver.websocks:
            self.funcserver.websocks[self.id] = None
            ioloop = tornado.ioloop.IOLoop.instance()
            ioloop.add_callback(lambda: self.funcserver.websocks.pop(self.id, None))

    def send_message(self, msg, binary=False):
        # TODO: check if following two lines are required
        # tornado documentation seems to indicate that
        # this might be handled internally.
        if not isinstance(msg, str):
            msg = json.dumps(msg)

        try:
            if self.ws_connection:
                self.write_message(msg, binary=binary)
        except tornado.iostream.StreamClosedError:
            self.on_close()

    @property
    def is_buffer_full(self):
        bsize = sum([len(x) for x in self.stream._write_buffer])
        return bsize >= self.WRITE_BUFFER_THRESHOLD

    def _msg_from(self, msg):
        return {'type': msg.get('type', ''), 'id': msg['id']}


def call(fn):
    ioloop = tornado.ioloop.IOLoop.instance()
    ioloop.add_callback(fn)


def make_handler(template, handler):
    class SimpleHandler(handler):
        def get(self):
            return self.render(template)

    return SimpleHandler


def resolve_path(path):
    return path if os.path.isabs(path) else os.path.join(os.path.dirname(__file__), path)


class WebLogHandler(logging.Handler):
    def __init__(self, funcserver):
        super(WebLogHandler, self).__init__()
        self.funcserver = funcserver

    def emit(self, record):
        msg = self.format(record)
        self.funcserver._send_log(msg)


class TemplateLoader(BaseLoader):
    def __init__(self, dirs=None, **kwargs):
        super(TemplateLoader, self).__init__(**kwargs)
        self.dirs = dirs or []

    def add_dir(self, d):
        self.dirs.append(d)

    def del_dir(self, d):
        self.dirs.remove(d)

    def resolve_path(self, name, parent_path=None):
        for d in reversed(self.dirs):
            p = os.path.join(d, name)
            if not os.path.exists(p): continue
            return os.path.abspath(p)

        return name

    def _create_template(self, name):
        f = open(name, 'rb')
        template = Template(f.read(), name=name, loader=self)
        f.close()
        return template


class CustomStaticFileHandler(StaticFileHandler):
    PATHS = []

    @classmethod
    def get_absolute_path(cls, root, path):
        for p in reversed(cls.PATHS):
            ap = os.path.join(p, path)
            if not os.path.exists(ap):
                continue
            return ap

        return path

    def validate_absolute_path(self, root, absolute_path):
        if (os.path.isdir(absolute_path) and
                self.default_filename is not None):
            # need to look at the request.path here for when path is empty
            # but there is some prefix to the path that was already
            # trimmed by the routing
            if not self.request.path.endswith("/"):
                self.redirect(self.request.path + "/", permanent=True)
                return
            absolute_path = os.path.join(absolute_path, self.default_filename)
        if not os.path.exists(absolute_path):
            raise HTTPError(404)
        if not os.path.isfile(absolute_path):
            raise HTTPError(403, "%s is not a file", self.path)
        return absolute_path

class StatsCollector(object):
    STATS_FLUSH_INTERVAL = 1

    def __init__(self, prefix, stats_loc):
        self.cache = {}
        self.gauge_cache = {}

        self.stats = None
        if not stats_loc: return

        port = None
        if ':' in stats_loc:
            ip, port = stats_loc.split(':')
            port = int(port)
        else:
            ip = stats_loc

        S = statsd.StatsClient
        self.stats = S(ip, port, prefix) if port is not None else S(ip, prefix=prefix)

        def fn():
            while 1:
                time.sleep(self.STATS_FLUSH_INTERVAL)
                self._collect_ramusage()
                self.send()

        self.stats_thread = gevent.spawn(fn)

    def incr(self, key, n=1):
        if self.stats is None: return
        self.cache[key] = self.cache.get(key, 0) + n

    def decr(self, key, n=1):
        if self.stats is None: return
        self.cache[key] = self.cache.get(key, 0) - n

    def timing(self, key, ms):
        if self.stats is None: return
        return self.stats.timing(key, ms)

    def gauge(self, key, n):
        self.gauge_cache[key] = n

    def _collect_ramusage(self):
        self.gauge('resource.maxrss',
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)

    def send(self):
        if self.stats is None: return
        p = self.stats.pipeline()

        for k, v in self.cache.iteritems():
            p.incr(k, v)

        for k, v in self.gauge_cache.iteritems():
            p.gauge(k, v)

        p.send()
        self.cache = {}
        self.gauge_cache = {}

class BaseScript(object):
    LOG_FORMATTER = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    DESC = 'Base script abstraction'

    def __init__(self):
        # argparse parser obj
        self.parser = argparse.ArgumentParser(description=self.DESC)
        self.define_baseargs(self.parser)
        self.define_args(self.parser)
        self.args = self.parser.parse_args()

        self.hostname = socket.gethostname()

        self.log = self.init_logger(self.args.log, self.args.log_level,\
            quiet=self.args.quiet)

        self.stats = self.create_stats()
        self.log.debug('init: args=%s' % repr(self.args))

    @property
    def name(self):
        return '.'.join([x for x in (sys.argv[0].split('.')[0], self.args.name) if x])

    def create_stats(self):
        stats_prefix = '.'.join([x for x in (self.hostname, self.name) if x])
        return StatsCollector(stats_prefix, self.args.statsd_server)

    def init_logger(self, fname, log_level, quiet=False):
        if not fname:
            fname = '%s.log' % self.name

        log = logging.getLogger('')

        stderr_hdlr = logging.StreamHandler(sys.stderr)
        rofile_hdlr = logging.handlers.RotatingFileHandler(fname,
            maxBytes=MAX_LOG_FILE_SIZE, backupCount=10)
        hdlrs = (stderr_hdlr, rofile_hdlr)

        for hdlr in hdlrs:
            hdlr.setFormatter(self.LOG_FORMATTER)
            log.addHandler(hdlr)

        log.addHandler(rofile_hdlr)
        if not quiet: log.addHandler(stderr_hdlr)

        log.setLevel(getattr(logging, log_level.upper()))

        return log

    def define_baseargs(self, parser):
        parser.add_argument('--name', default=None,
            help='Name to identify this instance')
        parser.add_argument('--statsd-server', default=None,
            help='Location of StatsD server to send statistics. '
                'Format is ip[:port]. Eg: localhost, localhost:8125')
        parser.add_argument('--log', default=None,
            help='Name of log file')
        parser.add_argument('--log-level', default='WARNING',
            help='Logging level as picked from the logging module')
        parser.add_argument('--quiet', action='store_true')

    def define_args(self, parser):
        pass

    def dump_stacks(self):
        '''
        Dumps the stack of all threads and greenlets. This function
        is meant for debugging. Useful when a deadlock happens.

        borrowed from: http://blog.ziade.org/2012/05/25/zmq-and-gevent-debugging-nightmares/
        '''

        dump = []

        # threads
        threads = dict([(th.ident, th.name)
                            for th in threading.enumerate()])

        for thread, frame in sys._current_frames().items():
            if thread not in threads: continue
            dump.append('Thread 0x%x (%s)\n' % (thread, threads[thread]))
            dump.append(''.join(traceback.format_stack(frame)))
            dump.append('\n')

        # greenlets
        try:
            from greenlet import greenlet
        except ImportError:
            return ''.join(dump)

        # if greenlet is present, let's dump each greenlet stack
        for ob in gc.get_objects():
            if not isinstance(ob, greenlet):
                continue
            if not ob:
                continue   # not running anymore or not started
            dump.append('Greenlet\n')
            dump.append(''.join(traceback.format_stack(ob.gr_frame)))
            dump.append('\n')

        return ''.join(dump)


class FuncServer(BaseScript):
    NAME = 'FuncServer'
    DESC = 'Default Functionality Server'
    DEFAULT_PORT = 9345
    VIRTUAL_HOST = r'.*'

    STATIC_PATH = 'static'
    TEMPLATE_PATH = 'templates'

    APP_CLASS = tornado.web.Application

    def __init__(self):
        super(FuncServer, self).__init__()
        self.log_id = 0

        # add weblog handler to logger
        weblog_hdlr = WebLogHandler(self)
        weblog_hdlr.setFormatter(self.LOG_FORMATTER)
        self.log.addHandler(weblog_hdlr)

        # tornado app object
        base_handlers = self.prepare_base_handlers()
        handlers = self.prepare_handlers()
        self.template_loader = TemplateLoader([resolve_path(self.TEMPLATE_PATH)])
        _ = self.prepare_template_loader(self.template_loader)
        if _ is not None: self.template_loader = _

        shclass = CustomStaticFileHandler
        shclass.PATHS.append(resolve_path(self.STATIC_PATH))
        _ = self.prepare_static_paths(shclass.PATHS)
        if _ is not None: shclass.PATHS = _

        self.static_handler_class = shclass

        self.nav_tabs = [('Console', '/console'), ('Logs', '/logs')]
        self.nav_tabs = self.prepare_nav_tabs(self.nav_tabs)

        settings = {
            'static_path': '<DUMMY-INEXISTENT-PATH>',
            'static_handler_class': self.static_handler_class,
            'template_loader': self.template_loader,
        }

        all_handlers = handlers + base_handlers
        self.app = self.APP_CLASS(**settings)
        self.app.add_handlers(self.VIRTUAL_HOST, all_handlers)

        sys.funcserver = self.app.funcserver = self

        # all active websockets and their state
        self.websocks = {}

    @property
    def name(self):
        return '.'.join([x for x in (self.NAME, self.args.name) if x])

    def define_baseargs(self, parser):
        super(FuncServer, self).define_baseargs(parser)
        parser.add_argument('--port', default=self.DEFAULT_PORT,
            type=int, help='port to listen on for server')

    def _send_log(self, msg):
        msg = {'type': MSG_TYPE_LOG, 'id': self.log_id, 'data': msg}

        bad_ws = []

        for _id, ws in self.websocks.iteritems():
            if ws is None: bad_ws.append(_id); continue
            ws['sock'].send_message(msg)

        for _id in bad_ws: del self.websocks[_id]

        self.log_id += 1

    def prepare_base_handlers(self):
        # Tornado URL handlers for core functionality

        return [
            (r'/ws', WSConnection),
            (r'/logs', make_handler('logs.html', BaseHandler)),
            (r'/console', make_handler('console.html', BaseHandler)),
            (r'/', make_handler('console.html', BaseHandler))
        ]

    def prepare_handlers(self):
        # Tornado URL handlers for additional functionality
        return []

    def prepare_template_loader(self, loader):
        # add additional template dirs by using
        # loader.add_dir(path)
        return loader

    def prepare_static_paths(self, paths):
        # add static paths that can contain
        # additional of override files
        # eg: paths.append(PATH)
        return paths

    def prepare_nav_tabs(self, nav_tabs):
        # Add additional tab buttons in the UI toolbar
        # eg: nav_tabs.append(('MyTab', '/mytab'))
        return nav_tabs

    def define_python_namespace(self):
        return {'server': self, 'logging': logging, 'call': call}

    def define_template_namespace(self):
        return self.define_python_namespace()

    def pre_start(self):
        '''
        Override to perform any operations
        before the server loop is started
        '''
        pass

    def start(self):
        self.pre_start()
        if self.args.port != 0:
            self.app.listen(self.args.port)
        tornado.ioloop.IOLoop.instance().start()

class RPCHandler(BaseHandler):
    WRITE_CHUNK_SIZE = 4096

    def initialize(self, server):
        self.server = server
        self.stats = server.stats
        self.log = server.log
        self.api = server.api

    def _get_apifn(self, fn_name):
        obj = self.api
        for part in fn_name.split('.'):
            obj = getattr(obj, part)
        return obj

    def _handle_single_call(self, m):
        fn_name = m.get('fn', None)
        sname = 'api.%s' % fn_name
        t = time.time()

        try:
            fn = self._get_apifn(fn_name)
            self.stats.incr(sname)
            r = fn(*m['args'], **m['kwargs'])
            r = {'success': True, 'result': r}
        except Exception, e:
            self.log.exception('Exception during RPC call. '
                'fn=%s, args=%s, kwargs=%s' % \
                (m.get('fn', ''), repr(m.get('args', '[]')),
                    repr(m.get('kwargs', '{}'))))
            r = {'success': False, 'result': repr(e)}
        finally:
            tdiff = (time.time() - t) * 1000
            self.stats.timing(sname, tdiff)

        return r

    def _handle_call(self, fn, m, protocol):
        if fn != '__batch__':
            r = self._handle_single_call(m)
        else:
            r = []
            for call in m['calls']:
                _r = self._handle_single_call(call)
                _r = _r['result'] if _r['success'] else None
                r.append(_r)

        r = self.get_serializer(protocol)(r)
        self.set_header('Content-Type', self.get_mime(protocol))
        self.set_header('Content-Length', len(r))

        chunk_size = self.WRITE_CHUNK_SIZE
        for i in xrange(0, len(r), chunk_size):
            self.write(r[i:i+chunk_size])
            self.flush()
        self.finish()

    def get_serializer(self, name):
        return {'msgpack': msgpack.packb,
                'json': json.dumps,
                'python': repr}.get(name, self.server.SERIALIZER)

    def get_deserializer(self, name):
        return {'msgpack': msgpack.packb,
                'json': json.loads,
                'python': eval}.get(name, self.server.DESERIALIZER)

    def get_mime(self, name):
        return {'msgpack': 'application/x-msgpack',
                'json': 'application/json',
                'python': 'application/x-python'}\
                .get(name, self.server.MIME)

    @tornado.web.asynchronous
    def post(self, protocol='default'):
        ref_int = random.choice(range(0,100000))
        self.log.debug('Entered post function %d ...' % ref_int)
        m = self.get_deserializer(protocol)(self.request.body)
        fn = m['fn']
        gevent.spawn(lambda: self._handle_call(fn, m, protocol))
        self.log.debug('Quitting post function %d ...' % ref_int)

    def failsafe_json_decode(self, v):
        try: v = json.loads(v)
        except ValueError: pass
        return v

    @tornado.web.asynchronous
    def get(self, protocol='default'):
        ref_int = random.choice(range(0,100000))
        self.log.debug('Entered get function %d ...' % ref_int)
        D = self.failsafe_json_decode
        args = dict([(k, D(v[0])) for k, v in self.request.arguments.iteritems()])

        fn = args.pop('fn')
        m = dict(kwargs=args, fn=fn, args=[])
        gevent.spawn(lambda: self._handle_call(fn, m, protocol))
        self.log.debug('Quitting get function %d ...' % ref_int)

class RPCServer(FuncServer):
    NAME = 'RPCServer'
    DESC = 'Default RPC Server'

    SERIALIZER = staticmethod(msgpack.packb)
    DESERIALIZER = staticmethod(msgpack.unpackb)
    MIME = 'application/x-msgpack'

    def __init__(self, *args, **kwargs):
        super(RPCServer, self).__init__(*args, **kwargs)
        self.api = None

    def pre_start(self):
        self.api = self.prepare_api()
        if not hasattr(self.api, 'log'): self.api.log = self.log
        super(RPCServer, self).pre_start()

    def prepare_api(self):
        '''
        Prepare the API object that is exposed as
        functionality by the RPCServer
        '''
        return None

    def prepare_base_handlers(self):
        hdlrs = super(RPCServer, self).prepare_base_handlers()
        hdlrs.append((r'/rpc(?:/([^/]*)/?)?', RPCHandler, dict(server=self)))
        return hdlrs

    def define_python_namespace(self):
        ns = super(RPCServer, self).define_python_namespace()
        ns['api'] = self.api
        return ns

def _passthrough(name):
    def fn(self, *args, **kwargs):
        p = self.prefix + '.' + name
        if self.bound or self.parent is None:
            return self._call(p, args, kwargs)
        else:
            return self.parent._call(p, args, kwargs)
    return fn

class RPCClient(object):
    SERIALIZER = staticmethod(msgpack.packb)
    DESERIALIZER = staticmethod(msgpack.unpackb)

    def __init__(self, server_url, prefix=None, parent=None):
        self.server_url = server_url
        self.rpc_url = urlparse.urljoin(server_url, 'rpc')
        self.is_batch = False
        self.prefix = prefix
        self.parent = parent
        self.bound = False
        self._calls = []

    def __getattr__(self, attr):
        prefix = self.prefix + '.' + attr if self.prefix else attr
        return self.__class__(self.server_url, prefix=prefix,
                parent=self if self.bound else self.parent)

    def get_handle(self):
        self.bound = True
        return self

    def __call__(self, *args, **kwargs):
        if self.bound or self.parent is None:
            return self._call(self.prefix, args, kwargs)
        else:
            return self.parent._call(self.prefix, args, kwargs)

    def _call(self, fn, args, kwargs):
        if not self.is_batch:
            return self._do_single_call(fn, args, kwargs)
        else:
            self._calls.append(dict(fn=fn, args=args, kwargs=kwargs))

    __getitem__ = _passthrough('__getitem__')
    __setitem__ = _passthrough('__setitem__')
    __delitem__ = _passthrough('__delitem__')
    __contains__ = _passthrough('__contains__')
    __len__ = _passthrough('__len__')

    def __nonzero__(self): return True

    def set_batch(self):
        self.is_batch = True

    def unset_batch(self):
        self.is_batch = False

    def _do_single_call(self, fn, args, kwargs):
        m = self.SERIALIZER(dict(fn=fn, args=args, kwargs=kwargs))
        req = requests.post(self.rpc_url, data=m)
        res = self.DESERIALIZER(req.content)

        if not res['success']:
            raise RPCCallException(res['result'])
        else:
            return res['result']

    def execute(self):
        if not self._calls: return

        m = dict(fn='__batch__', calls=self._calls)
        m = self.SERIALIZER(m)
        req = requests.post(self.rpc_url, data=m)
        res = self.DESERIALIZER(req.content)
        self._calls = []

        return res

if __name__ == '__main__':
    funcserver = FuncServer()
    funcserver.start()

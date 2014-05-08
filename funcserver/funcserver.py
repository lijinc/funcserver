from gevent import monkey; monkey.patch_all()

import gc
import os
import sys
import json
import code
import logging
import msgpack
import cStringIO
import urlparse
import argparse
import traceback
import threading

import requests
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.iostream

MSG_TYPE_CONSOLE = 0
MSG_TYPE_LOG = 1

MAX_LOG_FILE_SIZE = 100 * 1024 * 1024 # 100MB

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

class ConsoleHandler(tornado.web.RequestHandler):
    def __init__(self, *args, **kwargs):
        super(ConsoleHandler, self).__init__(*args, **kwargs)

    def get(self):
        pass

def resolve_path(path):
    return path if os.path.isabs(path) else os.path.join(os.path.dirname(__file__), path)

class WebLogHandler(logging.Handler):
    def __init__(self, funcserver):
        super(WebLogHandler, self).__init__()
        self.funcserver = funcserver

    def emit(self, record):
        msg = self.format(record)
        self.funcserver._send_log(msg)

class FuncServer(object):
    NAME = 'FuncServer'
    DESC = 'Default Functionality Server'
    DEFAULT_PORT = 9345

    STATIC_PATH = 'static'
    TEMPLATE_PATH = 'templates'

    def __init__(self):
        # argparse parser obj
        self.parser = argparse.ArgumentParser(description=self.DESC)
        self.define_baseargs(self.parser)
        self.define_args(self.parser)
        self.args = self.parser.parse_args()

        # prep logger
        self.log = self.init_logger(self.args.log)
        self.log_id = 0

        # tornado app object
        base_handlers = self.prepare_base_handlers()
        handlers = self.prepare_handlers()

        settings = {
            'static_path': resolve_path(self.STATIC_PATH),
            'template_path': resolve_path(self.TEMPLATE_PATH)
        }

        self.app = tornado.web.Application(handlers + base_handlers, **settings)
        sys.funcserver = self.app.funcserver = self

        # all active websockets and their state
        self.websocks = {}

    def init_logger(self, fname):
        log = logging.getLogger('')
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

        stderr_hdlr = logging.StreamHandler(sys.stderr)
        weblog_hdlr = WebLogHandler(self)
        rofile_hdlr = logging.handlers.RotatingFileHandler(fname,
            maxBytes=MAX_LOG_FILE_SIZE, backupCount=10)

        stderr_hdlr.setFormatter(formatter)
        weblog_hdlr.setFormatter(formatter)
        rofile_hdlr.setFormatter(formatter)

        log.addHandler(stderr_hdlr)
        log.addHandler(weblog_hdlr)
        log.addHandler(rofile_hdlr)

        log.setLevel(logging.DEBUG)

        return log

    def _send_log(self, msg):
        msg = {'type': MSG_TYPE_LOG, 'id': self.log_id, 'data': msg}

        for ws in self.websocks.itervalues():
            ws['sock'].send_message(msg)

        self.log_id += 1

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

    def define_baseargs(self, parser):
        parser.add_argument('--port', default=self.DEFAULT_PORT,
            type=int, help='port to listen on for server')
        parser.add_argument('--log', default='%s.log' % sys.argv[0].split('.')[0],
            help='Name of log file')

    def define_args(self, parser):
        pass

    def define_python_namespace(self):
        return {'server': self, 'logging': logging, 'call': call}

    def define_template_namespace(self):
        return self.define_python_namespace()

    def pre_start(self):
        self.log.debug('pre_start: args=%s' % repr(self.args))

    def start(self):
        self.pre_start()
        self.app.listen(self.args.port)
        tornado.ioloop.IOLoop.instance().start()

class RPCHandler(BaseHandler):
    def initialize(self, server):
        self.server = server
        self.api = server.api

    def post(self):
        m = msgpack.unpackb(self.request.body)

        try:
            r = getattr(self.api, m['fn'])(*m['args'], **m['kwargs'])
            r = {'success': True, 'result': r}
        except Exception, e:
            if hasattr(self, 'log'):
                self.log.exception('Exception during RPC call. '
                    'fn=%s, args=%s, kwargs=%s' % \
                    (m['fn'], repr(m['args']), repr(m['kwargs']))
            r = {'success': False, 'result': repr(e)}

        self.write(msgpack.packb(r))

class RPCServer(FuncServer):
    NAME = 'RPCServer'
    DESC = 'Default RPC Server'

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
        hdlrs.append((r'/rpc', RPCHandler, dict(server=self)))
        return hdlrs

    def define_python_namespace(self):
        ns = super(RPCServer, self).define_python_namespace()
        ns['api'] = self.api
        return ns

class RPCClientFunc(object):
    def __init__(self, client, fn):
        self.client = client
        self.fn = fn

    def __call__(self, *args, **kwargs):
        return self.client.call(self.fn, *args, **kwargs)

class RPCClient(object):
    def __init__(self, server_url):
        self.server_url = server_url
        self.rpc_url = urlparse.urljoin(server_url, 'rpc')

    def __getattr__(self, attr):
        return RPCClientFunc(self, attr)

    def call(self, fn, *args, **kwargs):
        m = msgpack.packb(dict(fn=fn, args=args, kwargs=kwargs))
        req = requests.post(self.rpc_url, data=m)
        res = msgpack.unpackb(req.content)

        if not res['success']:
            raise RPCCallException(res['result'])
        else:
            return res['result']

if __name__ == '__main__':
    funcserver = FuncServer()
    funcserver.start()

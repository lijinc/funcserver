import os
import sys
import json
import code
import logging
import cStringIO
import argparse

import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.iostream

MSG_TYPE_CONSOLE = 0
MSG_TYPE_LOG = 1

class BaseHandler(tornado.web.RequestHandler):
    def get_template_namespace(self):
        ns = super(BaseHandler, self).get_template_namespace()
        ns.update(sys.funcserver.define_template_namespace())
        return ns

class PyInterpreter(code.InteractiveInterpreter):
    def __init__(self, *args, **kwargs):
        code.InteractiveInterpreter.__init__(self, *args, **kwargs)
        self.output = ''

    def write(self, data):
        self.output = data

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
            interpreter.output = ''
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
    DEFAULT_PORT = 8889

    STATIC_PATH = 'static'
    TEMPLATE_PATH = 'templates'

    def __init__(self):
        self.log = self.init_logger()
        self.log_id = 0

        # argparse parser obj
        self.parser = argparse.ArgumentParser(description=self.DESC)
        self.define_baseargs(self.parser)
        self.define_args(self.parser)
        self.args = self.parser.parse_args()

        # tornado app object
        base_handlers = self.prepare_base_handlers()
        handlers = self.prepare_handlers()

        settings = {
            'static_path': resolve_path(self.STATIC_PATH),
            'template_path': resolve_path(self.TEMPLATE_PATH)
        }

        self.app = tornado.web.Application(base_handlers + handlers, **settings)
        sys.funcserver = self.app.funcserver = self

        # all active websockets and their state
        self.websocks = {}

        self.pre_start()

    def init_logger(self):
        log = logging.getLogger('')
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

        stderr_hdlr = logging.StreamHandler(sys.stderr)
        weblog_hdlr = WebLogHandler(self)

        stderr_hdlr.setFormatter(formatter)
        weblog_hdlr.setFormatter(formatter)

        log.addHandler(stderr_hdlr) 
        log.addHandler(weblog_hdlr) 

        log.setLevel(logging.WARNING)

        return log

    def _send_log(self, msg):
        msg = {'type': MSG_TYPE_LOG, 'id': self.log_id, 'data': msg}

        for ws in self.websocks.itervalues():
            ws['sock'].send_message(msg)

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

    def define_baseargs(self, parser):
        parser.add_argument('--port', default=self.DEFAULT_PORT,
            type=int, help='port to listen on for server')

    def define_args(self, parser):
        pass

    def define_python_namespace(self):
        return {'server': self, 'logging': logging}

    def define_template_namespace(self):
        return self.define_python_namespace()

    def pre_start(self):
        pass

    def start(self):
        self.app.listen(self.args.port)
        tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
    funcserver = FuncServer()
    funcserver.start()

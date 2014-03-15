import json
from funcserver import FuncServer, BaseHandler

class CalcAPI(object):
    def __init__(self, ignore_divbyzero=False):
        self.ignore_divbyzero = ignore_divbyzero

    def add(self, a, b):
        '''Computes the sum of @a and @b'''
        return a + b

    def sub(self, a, b):
        '''Computes the difference of @a and @b'''
        return a - b

    def mul(self, a, b):
        '''Computes the product of @a and @b'''
        return a * b

    def div(self, a, b):
        '''Computes the division of @a by @b'''
        if self.ignore_divbyzero: return 0
        return a / b

class CalcHandler(BaseHandler):
    def initialize(self, server):
        self.api = server.api

    def get(self, cmd, a, b):
        r = json.dumps(getattr(self.api, cmd)(eval(a), eval(b)))
        self.write(r)

class CalcServer(FuncServer):
    NAME = 'CalcServer'
    DESC = 'Calculation Server'

    def __init__(self):
        super(CalcServer, self).__init__()
        self.api = CalcAPI(self.args.ignore_divbyzero)

    def prepare_handlers(self):
        return [(r'/api/([a-z]+)/([\d\.]+)/([\d\.]+)', CalcHandler, dict(server=self))]

    def define_args(self, parser):
        parser.add_argument('--ignore-divbyzero', default=False,
            action='store_true',
            help='Ignore division by zero errors')

    def define_python_namespace(self):
        ns = super(CalcServer, self).define_python_namespace()
        ns['api'] = self.api
        return ns

if __name__ == '__main__':
    CalcServer().start()

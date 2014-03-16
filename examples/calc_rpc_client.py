from funcserver import RPCClient

def main():
    c = RPCClient('http://localhost:8889')
    print c.add(10, 20)
    print c.sub(10, 20)
    print c.mul(10, 20)
    print c.div(10, 20.0)

if __name__ == '__main__':
    main()

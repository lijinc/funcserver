# FuncServer

An abstraction to implement web accessible servers hosting any sort of functionality. This is built on a Tornado core and supports interacting with the server using a web based python terminal making debugging and maintenance easy. In addition the logs emitted by the process can be viewed from within the web interface.

![Image](./calcserver.png?raw=true)

## Installation
``` bash
sudo pip install git+git://github.com/prashanthellina/funcserver.git
```

## Usage

### Basic example

The following is the code to implement the most basic Functionality Server.

``` python
from funcserver import FuncServer

if __name__ == '__main__':
    FuncServer().start()
```

Run it by doing

``` bash
python example.py
```

This server is now started and listening on default port 8889 for commands. You can interact with it using the Web UI by visiting http://localhost:8889/

If you want to start it on a different port, do

``` bash
python example.py --port <port no>
```

### Things to do in the Console

``` python
# see the objects available in the console env
>>> dir()

# write a message to log (open the log tab in a new browser window
# to see the logged message being echoed back). you can use the log
# tab to observe all the logs being written by the application.
>>> server.log.warning('something is happening')

# set a different log level
>>> server.log.setLevel(logging.DEBUG)

# you can import any python module here
>>> import datetime
```

### Calculation server (another example)

You will find an example script in examples/ called calc_server.py. Let us run that and interact with it.

``` bash
python examples/calc_server.py
```

Open http://localhost:8889 in the browser to access the console.

``` python
# view the objects present in console env
>>> dir()

# Let us interact with the `api` object from here
>>> api.add(10, 20)
>>> api.mul(10, 20)
>>> api.div(10, 20.0)
```

Now let us use the CalcServer API over HTTP. Open the following links in the browser.

```
http://localhost:8899/api/add/10/20
http://localhost:8899/api/mul/10/20
http://localhost:8899/api/div/10/20.0
http://localhost:8899/api/div/10/0
```

The last URL must've caused an internal server error because we tried performing an illegal math operation.

This CalcServer has been configured to take an additional command-line parameter to ignore division by zero errors. Let us use that to prevent the error from being raised. Run CalcServer as follows

``` bash
python examples/calc_server.py --ignore-divbyzero
```

Now retry the last division api call URL in the browser.

examples/calc_server.py is a demonstration of how FuncServer can be used and what it offers. To understand more read the code in funcserver.py - It is very concise.

### Calculation RPC Server

You will find an example script in examples/ called calc_rpc_server.py. This is very similar to calc_server.py but uses the RPC mechanism provided by the framework. This RPC mechanism using msgpack as the serialization format and is meant to be used from Python clients primarily (It is possible to access from Javascript and other languages too however).

``` bash
python examples/calc_rpc_server.py
```

To use the server's functionality, run the provided example client script in examples/ directory.

``` bash
python examples/calc_rpc_client.py
```

### Debugging using PDB

When it is required to debug the API code using the Python debugger you may have to trigger the API function from the web based python console. However due to the design of FuncServer PDB does not work well in the scenario (as a result of the output being captured by the python interpretation part of FuncServer). To work around this issue a facility has been provided in the form of the "call" utility function available in the python console namespace. The usage is show below.

Let us assume that you have pdb trace set in code as follows:
``` python
def some_api_fn(self, a, b):
    import pdb; pdb.set_trace()
    c = a + b
    return c
```

If you call this api function as follows then debugging will not work and the api call will block from the console.
``` python
>>> api.some_api_fn(10, 20)
```

Instead do this:
``` python
>>> call(lambda: api.some_api_fn(10, 20))
```

Now the pdb console will appear in the terminal where you started your server.

### Projects using Funcserver

* [Rocks DB Server](https://github.com/prashanthellina/rocksdbserver) -- Server exposing facebook's Rocks DB API via RPC
* [Vowpal Wabbit Server](https://github.com/prashanthellina/vwserver) -- Server exposing Vowpal Wabbit ML utility API via RPC

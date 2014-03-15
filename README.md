# FuncServer

An abstraction to implement web accessible servers hosting any sort of functionality. This is built on a Tornado core and supports interacting with the server using a web based python terminal making debugging and maintenance easy. In addition the logs emitted by the process can be viewed from within the web interface.

## Installation
``` bash
sudo pip install git+git://github.com/prashanthellina/funcserver.git
```

## Usage

### Basic example

The following is the code to implement the most basic Functionality Server.

``` python
from funserver import FuncServer

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

You will find an example script in examples/ called calcserver.py. Let us run that and interact with it.

``` bash
python examples/calcserver.py
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
python examples/calcserver.py --ignore-divbyzero
```

Now retry the last division api call URL in the browser.

examples/calcserver.py is a demonstration of how FuncServer can be used and what it offers. To understand more read the code in funcserver.py - It is very concise.

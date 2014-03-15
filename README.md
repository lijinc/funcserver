FuncServer
==========

An abstraction to implement web accessible servers hosting any sort of functionality. This is built on a Tornado core and supports interacting with the server using a web based python terminal making debugging and maintenance easy. In addition the logs emitted by the process can be viewed from within the web interface.

Installation
------------
``` bash
sudo pip install git+git://github.com/prashanthellina/funcserver.git
```

Usage
-----

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

### Things to do in the Console



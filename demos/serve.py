"""Local dev server with correct MIME types for .mjs ES modules."""
import http.server
import functools
import os

http.server.SimpleHTTPRequestHandler.extensions_map['.mjs'] = 'application/javascript'

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory='.')
    server = http.server.HTTPServer(('localhost', 8080), handler)
    print('Serving demos at http://localhost:8080')
    server.serve_forever()

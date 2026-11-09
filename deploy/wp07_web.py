"""Non-root static approved UI and same-origin streaming API proxy."""
import functools,http.client,http.server,os
from select import select
from socket import MSG_PEEK
from urllib.parse import urlsplit

IMPORT_PATHS = {'/inputs/upload', '/inputs/url'}

class Proxy(http.server.SimpleHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.forward() if self.path.startswith('/api/') else super().do_GET()
    def do_HEAD(self):self.forward() if self.path.startswith('/api/') else super().do_HEAD()
    def do_POST(self):self.forward()
    def do_DELETE(self):self.forward()

    def wait_for_import_response(self, upstream):
        # The API owns the import deadline. Watch both sockets while it validates
        # and stores the video so stopping the browser request still cancels the wait.
        sockets = [upstream, self.connection]
        while True:
            readable, _, _ = select(sockets, [], [])
            if upstream in readable:
                return
            if not self.connection.recv(1, MSG_PEEK):
                raise ConnectionError('Import client disconnected')
            # A pipelined request is readable but belongs to the next exchange.
            sockets = [upstream]

    def forward(self):
        if not self.path.startswith('/api/'):self.send_error(404);return
        path = self.path[4:]
        is_import = self.command == 'POST' and path.split('?', 1)[0] in IMPORT_PATHS
        target=urlsplit(self.server.api_url);con=http.client.HTTPConnection(target.hostname,target.port,timeout=60)
        try:
            length=int(self.headers.get('Content-Length','0'))
            chunked=self.headers.get('Transfer-Encoding','').lower()=='chunked'
            headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','transfer-encoding')}
            con.putrequest(self.command,path,skip_accept_encoding=True)
            if is_import:
                # This HTTP server already handles the client's 100 Continue.
                headers = {key: value for key, value in headers.items() if key.lower() != 'expect'}
            for k,v in headers.items():con.putheader(k,v)
            if chunked:con.putheader('Transfer-Encoding','chunked')
            con.endheaders()
            if chunked:
                while True:
                    line=self.rfile.readline(8193)
                    if len(line)>8192 or not line.endswith(b'\r\n'):raise ConnectionError('Invalid chunk header')
                    size=int(line.split(b';',1)[0].strip(),16)
                    if size<0:raise ConnectionError('Invalid chunk length')
                    con.send(line)
                    if not size:
                        while True:
                            trailer=self.rfile.readline(8193)
                            if len(trailer)>8192 or not trailer.endswith(b'\r\n'):raise ConnectionError('Invalid trailer')
                            con.send(trailer)
                            if trailer==b'\r\n':break
                        break
                    left=size
                    while left:
                        data=self.rfile.read(min(65536,left))
                        if not data:raise ConnectionError('Interrupted chunk')
                        con.send(data);left-=len(data)
                    ending=self.rfile.read(2)
                    if ending!=b'\r\n':raise ConnectionError('Invalid chunk ending')
                    con.send(ending)
            remaining=0 if chunked else length
            while remaining:
                chunk=self.rfile.read(min(65536,remaining))
                if not chunk:break
                con.send(chunk);remaining-=len(chunk)
            if is_import:
                if remaining:raise ConnectionError('Interrupted request body')
                self.wait_for_import_response(con.sock)
            response=con.getresponse();self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in ('connection','transfer-encoding'):self.send_header(k,v)
            self.send_header('Connection','close');self.end_headers()
            if self.command!='HEAD':
                while True:
                    chunk=response.read1(65536)
                    if not chunk:break
                    self.wfile.write(chunk);self.wfile.flush()
        except (OSError,ValueError,http.client.HTTPException):
            self.close_connection=True
        finally:con.close();self.close_connection=True

def main():
    target=os.getenv('WP07_API_UPSTREAM','http://api:8000')
    u=urlsplit(target)
    if u.scheme!='http' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('Invalid private API upstream')
    handler=functools.partial(Proxy,directory='/site')
    server=http.server.ThreadingHTTPServer(('0.0.0.0',8080),handler)
    server.api_url=target
    server.serve_forever()

if __name__=='__main__':main()

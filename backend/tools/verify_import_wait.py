"""Exercise the built Web proxy against delayed, loopback-only synthetic imports."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
from threading import Event, Thread
import time

sys.path.insert(0, '/srv')
from wp07_web import Proxy

received_cancel_test = Event()
observed_disconnect = Event()


class API(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def respond(self, status, body):
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        assert self.rfile.read(int(self.headers['Content-Length'])) == b'synthetic'
        assert 'Expect' not in self.headers
        if self.path.endswith('?cancel=1'):
            received_cancel_test.set()
            if not self.rfile.read(1):
                observed_disconnect.set()
            return
        assert self.path in ('/inputs/upload', '/inputs/url')
        time.sleep(62)
        self.respond(201, b'{"status":"QUEUED"}')

    def do_GET(self):
        if self.path == '/slow':
            time.sleep(62)
            try:
                self.respond(200, b'slow')
            except OSError:
                pass
        else:
            self.respond(200, b'ok')


api = ThreadingHTTPServer(('127.0.0.1', 0), API)
web = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
web.api_url = f'http://127.0.0.1:{api.server_port}'
for server in (api, web):
    Thread(target=server.serve_forever, daemon=True).start()


def request(method, path, body=None, headers=None):
    connection = HTTPConnection('127.0.0.1', web.server_port, timeout=70)
    try:
        started = time.monotonic()
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, response.read(), time.monotonic() - started
    finally:
        connection.close()


def ordinary_timeout():
    started = time.monotonic()
    try:
        request('GET', '/api/slow')
    except RemoteDisconnected:
        elapsed = time.monotonic() - started
        assert 59 <= elapsed < 62, elapsed
        return round(elapsed, 1)
    raise AssertionError('Non-import response should still time out')


with ThreadPoolExecutor(max_workers=3) as pool:
    pending = [
        pool.submit(request, 'POST', '/api/inputs/upload', b'synthetic', {'Expect': '100-continue'}),
        pool.submit(request, 'POST', '/api/inputs/url', b'synthetic'),
    ]
    slow = pool.submit(ordinary_timeout)
    with socket.create_connection(('127.0.0.1', web.server_port), timeout=5) as browser:
        browser.sendall(b'POST /api/inputs/upload?cancel=1 HTTP/1.1\r\nHost: localhost\r\n'
                        b'Content-Length: 9\r\n\r\nsynthetic')
        assert received_cancel_test.wait(5), 'Upload did not reach the mock API'
    assert observed_disconnect.wait(5), 'Browser disconnect did not reach the mock API'
    assert request('GET', '/api/health/live')[:2] == (200, b'ok')
    for future in pending:
        status, body, elapsed = future.result()
        assert status == 201 and body == b'{"status":"QUEUED"}' and elapsed >= 61
        print(f'Import returned 201 after {elapsed:.1f} seconds', flush=True)
    print(f'Ordinary request timed out after {slow.result()} seconds and cancellation propagated', flush=True)

bundle = ''.join(path.read_text(encoding='utf-8') for path in Path('/site/assets').glob('*.js'))
assert '导入成功' in bundle and '上传完成，正在校验并保存视频…' in bundle

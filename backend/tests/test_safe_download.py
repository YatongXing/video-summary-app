"""Controlled HTTP/DNS tests: no request leaves the test process."""
import io
import socket
import pytest
from app import safe_download as download
from app.media import InputError

@pytest.mark.parametrize('url',['http://example.com/a','https://user:secret@example.com/a','https://localhost/a','https://x.localhost/a','https://example.com:8443/a','https://example.com/a#fragment','https://example.com/a\nHost:private','https://example.com\\@localhost/a'])
def test_unsafe_url_syntax_rejected_without_echo(url):
    with pytest.raises(InputError) as error:download.validate_url(url)
    assert url not in error.value.detail and 'secret' not in error.value.detail

@pytest.mark.parametrize('addresses',[['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['::1'],['fc00::1'],['fe80::1'],['::ffff:8.8.8.8'],['64:ff9b::808:808'],['2002:808:808::'],['2001:0:4136:e378:8000:63bf:3fff:fdd2'],['93.184.216.34','192.168.1.1']])
def test_all_dns_answers_must_be_public(monkeypatch,addresses):
    monkeypatch.setattr(download.socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in addresses])
    with pytest.raises(InputError):download.resolve_public('example.com')

def test_pinned_socket_preserves_original_tls_identity(monkeypatch):
    calls=[]
    class Sock:
        def close(self):calls.append('closed')
    sock=Sock()
    monkeypatch.setattr(download.socket,'create_connection',lambda address,timeout:(calls.append(address) or sock))
    connection=download.PinnedHTTPSConnection('example.com','93.184.216.34',5)
    class Context:
        def wrap_socket(self,s,server_hostname):calls.append(server_hostname);return s
    connection._context=Context();connection.connect()
    assert calls==[('93.184.216.34',443),'example.com']

class Response(io.BytesIO):
    def __init__(self,body=b'',status=200,headers=None):super().__init__(body);self.status=status;self.headers=headers or {}
    def getheader(self,name,default=None):return self.headers.get(name,default)

def fake_network(monkeypatch,responses):
    calls=[]
    monkeypatch.setattr(download,'resolve_public',lambda host:['93.184.216.34'])
    class Connection:
        def __init__(self,host,ip,timeout):calls.append((host,ip));self.response=responses.pop(0)
        def request(self,method,target,headers):calls.append((method,target,headers))
        def getresponse(self):return self.response
        def close(self):pass
    monkeypatch.setattr(download,'PinnedHTTPSConnection',Connection)
    return calls

def test_stream_byte_limit_includes_exact_boundary(monkeypatch,tmp_path):
    fake_network(monkeypatch,[Response(b'12345',headers={'Content-Type':'video/mp4'})])
    path=tmp_path/'input';download.download_video('https://example.com/a?signature=private',path,5)
    assert path.read_bytes()==b'12345'
    fake_network(monkeypatch,[Response(b'123456',headers={'Content-Type':'video/mp4'})])
    with pytest.raises(InputError) as error:download.download_video('https://example.com/a',path,5)
    assert error.value.code=='FILE_TOO_LARGE'

@pytest.mark.parametrize('response',[Response(b'',headers={'Content-Length':'6'}),Response(b'<html/>',headers={'Content-Type':'text/html'}),Response(b'x',headers={'Content-Length':'3'}),Response(b'x',headers={'Content-Encoding':'gzip'})])
def test_invalid_response_headers_and_incomplete_body(monkeypatch,tmp_path,response):
    fake_network(monkeypatch,[response])
    with pytest.raises(InputError):download.download_video('https://example.com/a',tmp_path/'input',5)

def test_redirect_revalidates_target_before_connection(monkeypatch,tmp_path):
    calls=fake_network(monkeypatch,[Response(status=302,headers={'Location':'https://127.0.0.1/internal'})])
    def resolve(host):
        if host=='127.0.0.1':raise InputError('UNSAFE_URL')
        return ['93.184.216.34']
    monkeypatch.setattr(download,'resolve_public',resolve)
    with pytest.raises(InputError):download.download_video('https://example.com/a',tmp_path/'input',5)
    assert len([entry for entry in calls if len(entry)==2])==1

def test_relative_redirect_and_no_credentials_forwarded(monkeypatch,tmp_path):
    calls=fake_network(monkeypatch,[Response(status=302,headers={'Location':'/video.mp4'}),Response(b'ok',headers={'Content-Type':'video/mp4','Content-Length':'2'})])
    download.download_video('https://example.com/a',tmp_path/'input',5)
    requests=[entry for entry in calls if len(entry)==3]
    assert requests[-1][1]=='/video.mp4'
    for _,_,headers in requests:assert not {'Cookie','Authorization','Proxy-Authorization'} & headers.keys()


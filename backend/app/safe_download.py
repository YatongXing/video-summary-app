"""Pinned-IP HTTPS download: DNS validation and the connected socket cannot diverge."""
from app import maintenance
import http.client
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin, unquote
from app.media import InputError

def validate_url(url: str):
    try:
        if len(url)>4096 or not url.isprintable() or '\\' in url: raise ValueError()
        p=urlsplit(url)
        if p.scheme!='https' or not p.hostname or p.username is not None or p.password is not None or p.port not in (None,443) or p.fragment: raise ValueError()
        host=p.hostname.encode('idna').decode('ascii')
        if host.rstrip('.').lower()=='localhost' or host.lower().endswith('.localhost'): raise ValueError()
        path=p.path or '/'
        if not path.isascii() or not p.query.isascii(): raise ValueError()
        return p,host,path+('?' + p.query if p.query else '')
    except (ValueError,UnicodeError): raise InputError('UNSAFE_URL','请提供合法的 HTTPS 直达视频链接。') from None

def resolve_public(host: str):
    try:
        rows=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses: raise ValueError()
        for value in addresses:
            ip=ipaddress.ip_address(value)
            if not ip.is_global or ip.is_multicast or ip.is_unspecified or getattr(ip,'ipv4_mapped',None) is not None: raise ValueError()
            if ip.version==6 and (ip.sixtofour is not None or ip.teredo is not None or ip in ipaddress.ip_network('64:ff9b::/96') or ip in ipaddress.ip_network('64:ff9b:1::/48')): raise ValueError()
        return addresses
    except (OSError,ValueError): raise InputError('UNSAFE_URL','链接地址不可访问或不符合安全要求。') from None

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,host,ip,timeout):
        super().__init__(host,port=443,timeout=timeout,context=ssl.create_default_context())
        self.pinned_ip=ip
    def connect(self):
        sock=socket.create_connection((self.pinned_ip,443),timeout=self.timeout)
        try: self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException: sock.close(); raise

def download_video(url: str,path: Path,max_bytes: int):
    maintenance.ensure_open()
    deadline=time.monotonic()+max(1,min(3600,int(os.getenv('IMPORT_DOWNLOAD_TIMEOUT_SECONDS','1800'))))
    current=url
    try:
        for redirect in range(4):
            maintenance.ensure_open()
            parts,host,target=validate_url(current)
            addresses=resolve_public(host)
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError()
            connection=PinnedHTTPSConnection(host,addresses[0],min(20,remaining))
            try:
                connection.request('GET',target,headers={'Accept':'video/*, application/octet-stream','Accept-Encoding':'identity','User-Agent':'VideoSummaryApp/1.0'})
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location or redirect==3: raise InputError('UNSAFE_URL','链接重定向不符合要求。')
                    current=urljoin(current,location); continue
                if response.status!=200: raise InputError('DOWNLOAD_FAILED','无法获取视频，请检查链接授权和有效期。')
                if response.getheader('Content-Encoding','identity').lower()!='identity': raise InputError('INVALID_MEDIA','链接未返回原始媒体文件。')
                media_type=response.getheader('Content-Type','').split(';')[0].strip().lower()
                if media_type and media_type not in ('application/octet-stream','binary/octet-stream') and not media_type.startswith('video/'):
                    raise InputError('INVALID_MEDIA','链接必须直接返回视频文件，不能是播放页面。')
                length=response.getheader('Content-Length')
                if length is not None:
                    try: expected=int(length)
                    except ValueError: raise InputError('DOWNLOAD_FAILED','下载响应无效。') from None
                    if expected<0: raise InputError('DOWNLOAD_FAILED','下载响应无效。')
                    if expected>max_bytes: raise InputError('FILE_TOO_LARGE','单个视频不能超过 2 GB。',413)
                else: expected=None
                total=0
                with path.open('wb') as destination:
                    while True:
                        maintenance.ensure_open()
                        if time.monotonic()>deadline: raise TimeoutError()
                        chunk=response.read(min(1024*1024,max_bytes-total+1))
                        maintenance.ensure_open()
                        if not chunk: break
                        total+=len(chunk)
                        if total>max_bytes: raise InputError('FILE_TOO_LARGE','单个视频不能超过 2 GB。',413)
                        destination.write(chunk)
                if expected is not None and expected!=total: raise InputError('DOWNLOAD_FAILED','视频下载不完整。')
                return
            finally: connection.close()
    except InputError: raise
    except (OSError,http.client.HTTPException,TimeoutError,ValueError): raise InputError('DOWNLOAD_FAILED','视频下载失败或超时，请检查链接。') from None

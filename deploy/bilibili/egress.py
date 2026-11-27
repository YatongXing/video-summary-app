"""HTTPS CONNECT policy. The downloader reaches this process over a Unix socket."""
import asyncio
from contextlib import suppress
import ipaddress
import os
import re
import socket

CONNECT = re.compile(rb'CONNECT ([a-z0-9.-]+):443 HTTP/1\.[01]\r\n', re.I)
API_HOSTS = {'www.bilibili.com', 'api.bilibili.com'}
CDN_DOMAINS = {'bilivideo.com', 'bilivideo.cn'}


def permitted(host):
    return host in API_HOSTS or any(host.endswith('.' + domain) for domain in CDN_DOMAINS)


async def public_ipv4(host):
    rows = await asyncio.get_running_loop().getaddrinfo(
        host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    addresses = {row[4][0] for row in rows}
    if not addresses:
        raise ValueError('No public address')
    for address in addresses:
        ip = ipaddress.IPv4Address(address)
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError('Address outside policy')
    return sorted(addresses)


async def pump(reader, writer, budget=160 * 1024 * 1024):
    while chunk := await reader.read(65536):
        budget -= len(chunk)
        if budget < 0:
            return
        writer.write(chunk)
        await writer.drain()


async def tunnel(left_reader, left_writer, right_reader, right_writer):
    tasks = [asyncio.create_task(pump(left_reader, right_writer)),
             asyncio.create_task(pump(right_reader, left_writer))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def connect(reader, writer):
    remote = None
    response = b'403 Forbidden'
    try:
        async with asyncio.timeout(8):
            match = CONNECT.fullmatch(await reader.readline())
            if not match:
                return
            host = match[1].decode('ascii').lower()
            while True:
                line = await reader.readline()
                if line == b'\r\n':
                    break
                if not line or b':' not in line:
                    return
            if not permitted(host):
                return
            addresses = await public_ipv4(host)
            response = b'502 Bad Gateway'
            upstream, remote = await asyncio.open_connection(addresses[0], 443, family=socket.AF_INET)
        writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await writer.drain()
        async with asyncio.timeout(95):
            await tunnel(reader, writer, upstream, remote)
    except (OSError, ValueError, TimeoutError, EOFError, asyncio.LimitOverrunError):
        pass
    finally:
        if remote:
            remote.close()
        else:
            writer.write(b'HTTP/1.1 ' + response + b'\r\nContent-Length: 0\r\n\r\n')
            with suppress(OSError):
                await writer.drain()
        writer.close()


async def main():
    path = '/run/bilibili-egress/connect.sock'
    with suppress(FileNotFoundError):
        os.unlink(path)
    async with await asyncio.start_unix_server(connect, path=path, limit=8192) as server:
        os.chmod(path, 0o660)
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())

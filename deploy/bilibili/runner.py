"""Run one isolated download and stream its result to the Backend Unix socket."""
import asyncio
from contextlib import suppress
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sys
import tempfile

from egress import tunnel
from bilibili_evidence import (
    AUDIO_BYTES, AUTO_DOWNLOAD_SECONDS, AUTO_RELAY_SECONDS, DISK_HEADROOM, FILES,
    MAX_TRANSCRIPT_BYTES, SUBTITLES, VISUAL_BYTES, VISUAL_DOWNLOAD_SECONDS, VISUAL_RELAY_SECONDS,
)

REQUEST = re.compile(rb'GET /(BV[A-Za-z0-9]{10})/([1-9][0-9]{0,3})/(auto|visual) HTTP/1\.[01]\r\n')
EXTRACTOR = Path(__file__).with_name('extract.py')
WORK = Path('/work')
busy = asyncio.Lock()


async def relay(reader, writer):
    remote = None
    try:
        upstream, remote = await asyncio.open_unix_connection('/run/bilibili-egress/connect.sock')
        await tunnel(reader, writer, upstream, remote)
    except OSError:
        pass
    finally:
        if remote:
            remote.close()
        writer.close()


async def write_error(writer, code, status=422):
    writer.write(f'HTTP/1.1 {status} Import failed\r\nX-Import-Error: {code}\r\n'
                 'Content-Length: 0\r\nConnection: close\r\n\r\n'.encode())
    await writer.drain()


async def receive_headers(reader):
    while True:
        line = await reader.readline()
        if line == b'\r\n':
            return
        if not line or b':' not in line:
            raise ValueError('Invalid headers')


async def run_extractor(bvid, part, mode, directory, disconnected):
    child = await asyncio.create_subprocess_exec(
        sys.executable, '-I', '-c',
        'import runpy,sys; sys.path.insert(0,"/opt/bilibili"); runpy.run_path(sys.argv.pop(1),run_name="__main__")',
        str(EXTRACTOR), bvid, part, mode, cwd=directory,
        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': directory, 'TMPDIR': directory,
             'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=None, start_new_session=True)
    output = asyncio.create_task(child.communicate())
    try:
        async with asyncio.timeout(VISUAL_DOWNLOAD_SECONDS if mode == 'visual' else AUTO_DOWNLOAD_SECONDS):
            await asyncio.wait((output, disconnected), return_when=asyncio.FIRST_COMPLETED)
            if disconnected.done():
                return None
            stdout, _ = output.result()
            if child.returncode or len(stdout) > 256:
                return {'error': 'UNAVAILABLE'}
            return json.loads(stdout)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        with suppress(TimeoutError):
            await asyncio.wait_for(output, 3)


async def handle(reader, writer):
    watcher = None
    response_started = False
    try:
        async with asyncio.timeout(3):
            match = REQUEST.fullmatch(await reader.readline())
            if not match:
                return
            await receive_headers(reader)
        if busy.locked():
            await write_error(writer, 'BUSY', 429)
            return
        bvid, part, mode = (item.decode('ascii') for item in match.groups())
        async with busy:
            watcher = asyncio.create_task(reader.read(1))
            visual = mode == 'visual'
            reserve = 2 * VISUAL_BYTES if visual else AUDIO_BYTES
            if shutil.disk_usage(WORK).free < reserve + DISK_HEADROOM:
                await write_error(writer, 'NO_SPACE', 503)
                return
            with tempfile.TemporaryDirectory(dir=WORK, prefix='import-') as scratch:
                async with asyncio.timeout(VISUAL_RELAY_SECONDS if visual else AUTO_RELAY_SECONDS):
                    result = await run_extractor(bvid, part, mode, scratch, watcher)
                    if result is None or watcher.done():
                        return
                    if 'error' in result:
                        await write_error(writer, result['error'])
                        return
                    kind = result['evidence']
                    subtitle_file = Path(scratch) / FILES[SUBTITLES]
                    subtitle = subtitle_file.read_bytes() if visual and subtitle_file.is_file() else b''
                    if len(subtitle) > MAX_TRANSCRIPT_BYTES:
                        await write_error(writer, 'NO_MEDIA')
                        return
                    with (Path(scratch) / FILES[kind]).open('rb') as media:
                        size = os.fstat(media.fileno()).st_size
                        limit = MAX_TRANSCRIPT_BYTES if kind == SUBTITLES else VISUAL_BYTES if visual else AUDIO_BYTES
                        if not 0 < size <= limit:
                            await write_error(writer, 'TOO_LARGE', 413)
                            return
                        response_started = True
                        writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {size + len(subtitle)}\r\n'
                                     f'X-Import-Subtitle-Bytes: {len(subtitle)}\r\n'
                                     f'X-Import-Source: {bvid}:{part}\r\n'
                                     f'X-Import-Duration: {result["duration"]}\r\n'
                                     f'X-Import-Evidence: {kind}\r\n'
                                     'Connection: close\r\n\r\n'.encode())
                        if subtitle:
                            writer.write(subtitle)
                            await writer.drain()
                        while not watcher.done() and (chunk := media.read(65536)):
                            writer.write(chunk)
                            await writer.drain()
    except TimeoutError:
        if not response_started and watcher is not None and not watcher.done():
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(write_error(writer, 'TIMEOUT', 504), 1)
    except (OSError, ValueError, KeyError):
        pass
    finally:
        if watcher is not None:
            watcher.cancel()
        writer.close()


async def serve():
    # The volume lock makes startup recovery exclusive with live imports.
    for path in WORK.iterdir():
        if re.fullmatch(r'import-[a-z0-9_]{8}', path.name) and not path.is_symlink() and path.is_dir():
            shutil.rmtree(path)
    path = '/run/bilibili-api/download.sock'
    with suppress(FileNotFoundError):
        os.unlink(path)
    async with await asyncio.start_server(relay, '127.0.0.1', 8080, limit=8192), \
            await asyncio.start_unix_server(handle, path=path, limit=8192) as server:
        os.chmod(path, 0o660)
        await server.serve_forever()


if __name__ == '__main__':
    with (WORK / '.downloader.lock').open('a') as ownership:
        fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(serve())

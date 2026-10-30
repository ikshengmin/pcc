"""Lifecycle adapter for the bundled macOS process transparent proxy."""

import asyncio
import ipaddress
import json
import os
from pathlib import Path
import sys

from . import proto


def listener_configuration(options):
    candidates = []
    for option, server in options:
        if not any(isinstance(p, proto.Socks5) for p in option.protos):
            continue
        if option.cipher or option.sslclient or option.unix:
            continue
        sockets = getattr(server, 'sockets', ())
        choices = []
        for sock in sockets:
            address, port = sock.getsockname()[:2]
            address = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(address, address)
            try:
                if not ipaddress.ip_address(address).is_loopback:
                    continue
            except ValueError:
                continue
            config = {'host': address, 'port': port}
            if option.users:
                try:
                    username, password = option.users[0].decode('utf-8').split(':', 1)
                except (UnicodeDecodeError, ValueError) as error:
                    raise ValueError('Process capture requires UTF-8 username:password SOCKS5 credentials') from error
                config.update(username=username, password=password)
            choices.append(config)
        if choices:
            candidates.append(next((c for c in choices if c['host'] == '127.0.0.1'), choices[0]))
    if len(candidates) != 1:
        raise ValueError('--process-rules requires exactly one plain loopback SOCKS5 listener; '
                         'use -l http+socks5://127.0.0.1:8082')
    return candidates[0]


def controller_path(app=None):
    root = Path(app or os.environ.get('PPROXY_MACOS_APP', '/Applications/PythonProxy.app'))
    executable = root / 'Contents/MacOS/PythonProxy'
    if not executable.is_file():
        raise ValueError('The bundled PythonProxy.app is missing. Build/install the python-proxy '
                         'macOS package or pass --capture-app /path/to/PythonProxy.app')
    return executable


class ProcessCapture:
    def __init__(self, process):
        self.process = process
        self.stopping = False
        self.failed = False

    @classmethod
    async def start(cls, config, app=None):
        if sys.platform != 'darwin':
            raise ValueError('--process-rules currently requires macOS')
        executable = controller_path(app)
        # The build receipt distinguishes a runnable signed package from a build
        # made only for compile/tests. Do not try to install an ad-hoc extension.
        receipt = executable.parents[1] / 'Resources/build.json'
        if not receipt.is_file() or not json.loads(receipt.read_text()).get('signed'):
            raise ValueError('Process capture needs a signed macOS package; this build is for validation only')
        process = await asyncio.create_subprocess_exec(
            str(executable), 'start', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=None,
        )
        capture = cls(process)
        try:
            process.stdin.write(json.dumps(config).encode())
            await process.stdin.drain()
            process.stdin.close()
            await asyncio.wait_for(capture._ready(), 310)
        except BaseException:
            await capture.close()
            raise
        return capture

    async def _ready(self):
        while True:
            line = await self.process.stdout.readline()
            if not line:
                code = await self.process.wait()
                raise RuntimeError('Process capture failed to start (controller exit %s)' % code)
            text = line.decode('utf-8', 'replace').rstrip()
            print(text, flush=True)
            if text.startswith('Process TCP/UDP capture active.'):
                return

    async def monitor(self, loop):
        code = await self.process.wait()
        if not self.stopping:
            self.failed = True
            print('Process capture stopped unexpectedly (exit %s); stopping the proxy' % code,
                  file=sys.stderr, flush=True)
            loop.stop()

    async def close(self):
        self.stopping = True
        if self.process.returncode is not None:
            return
        try:
            self.process.terminate()
        except ProcessLookupError:
            await self.process.wait()
            return
        try:
            await asyncio.wait_for(self.process.wait(), 15)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
            raise RuntimeError('Process controller did not stop; check macOS Network settings')

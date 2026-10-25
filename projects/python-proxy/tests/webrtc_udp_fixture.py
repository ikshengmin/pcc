"""Temporary loopback-only services for a real Chrome WebRTC relay test.

Run from projects/python-proxy with uv run --no-sync python tests/webrtc_udp_fixture.py.
The printed URLs and ports are ephemeral. No existing proxy listener is used.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pproxy import server
from test_socks5_udp import datagram, proxy
from test_proxy_stability import network
from test_turn_udp import turn_server


async def main():
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    async with network() as net, turn_server() as (turn_endpoint, turn), datagram() as trap:
        remote_port = await proxy(net)
        remote = server.proxies_by_uri(f'socks5://127.0.0.1:{remote_port}')
        local_port = await proxy(net, remotes=[remote], tcp_remotes=[remote])
        entry = server.proxies_by_uri(f'tunnel{{127.0.0.1:{turn_endpoint[1]}}}://127.0.0.1:{local_port}')
        udp, _ = await entry.udp_start_server(dict(urserver=[remote], block=None, salgorithm='fa'))
        counts = {'turn_datagrams': 0, 'turn_channels': 0}
        original = turn.datagram_received
        def receive(data, addr):
            counts['turn_datagrams'] += 1
            if data and data[0] & 0xC0 == 0x40:
                counts['turn_channels'] += 1
            original(data, addr)
        turn.datagram_received = receive
        metadata = {'turn_url': f'turn:127.0.0.1:{local_port}?transport=udp',
                    'username': 'user', 'credential': 'pass', 'trap_port': trap.addr[1],
                    'local_port': local_port, 'remote_socks_port': remote_port,
                    'remote_turn_port': turn_endpoint[1], 'pid': os.getpid()}
        template = Path(__file__).with_suffix('.html').read_text()
        html = template.replace('__FIXTURE__', json.dumps(metadata)).encode()
        async def http(reader, writer):
            try:
                request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                target = request.split(b' ', 2)[1]
                if target == b'/stats':
                    body = json.dumps({**counts, 'allocations': len(turn.allocations),
                                       'direct_stun_packets': trap.received.qsize()}).encode()
                    mime = 'application/json'
                else:
                    body, mime = html, 'text/html; charset=utf-8'
                csp = "default-src 'self'; script-src 'nonce-zeroomega-test'; style-src 'unsafe-inline'; media-src blob:"
                writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Security-Policy: {csp}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
                await writer.drain()
            finally:
                writer.close()
        http_port = await net.listen(http)
        metadata['test_url'] = f'http://127.0.0.1:{http_port}/'
        print(json.dumps(metadata), flush=True)
        try:
            await asyncio.wait_for(stopped.wait(), 900)
        finally:
            udp.close()
            await remote.udp_close()


if __name__ == '__main__':
    asyncio.run(main())

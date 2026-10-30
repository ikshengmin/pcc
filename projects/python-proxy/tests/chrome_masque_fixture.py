"""Owned, temporary local endpoints for native Chrome HTTP3/MASQUE tests."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aioquic.asyncio import QuicConnectionProtocol, serve as quic_serve
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pproxy import masque, server, socks5_udp
from test_masque_proxy import certificate
from test_proxy_stability import network
from test_socks5_udp import datagram, proxy
from test_turn_udp import turn_server


async def main():
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    counters = {'origin_h1': 0, 'origin_h3': 0, 'masque_tcp': 0, 'masque_udp': 0, 'origin_h3_peers': []}
    egress = set()
    original_resolve = loop.getaddrinfo
    async def resolve(host, *args, **kwargs):
        if host in ('h3.test', b'h3.test'):
            host = '127.0.0.1'
        return await original_resolve(host, *args, **kwargs)
    loop.getaddrinfo = resolve
    class ObservedRelay(socks5_udp.DirectRelay):
        async def send(self, *args):
            await super().send(*args)
            for transport in self.transports.values():
                egress.add(transport.get_extra_info('sockname')[1])
    socks5_udp.DirectRelay = ObservedRelay
    class ObservedMasque(masque.MasqueProtocol):
        def request(self, event):
            if event.stream_id not in self.flows:
                counters['masque_udp' if dict(event.headers).get(b':protocol') == b'connect-udp' else 'masque_tcp'] += 1
            super().request(event)
    masque.MasqueProtocol = ObservedMasque
    with tempfile.TemporaryDirectory(prefix='pcc-chrome-masque-', dir='/private/tmp') as temp:
        cert, key = certificate(Path(temp))
        public = x509.load_pem_x509_certificate(cert.read_bytes()).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        pin = base64.b64encode(hashlib.sha256(public).digest()).decode()
        async with network() as net, turn_server() as (turn_endpoint, turn), datagram() as trap:
            remote_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{remote_port}')
            local_port = await proxy(net, remotes=[remote], tcp_remotes=[remote])
            owner = server.proxies_by_uri(f'masque{{127.0.0.1:{turn_endpoint[1]}}}://127.0.0.1:{local_port}')
            owner.quicserver.load_cert_chain(str(cert), str(key))
            gateway = await owner.start_server(dict(rserver=[remote], urserver=[remote], salgorithm='fa', verbose=lambda text: print(text, flush=True)))
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(str(cert), str(key))
            tls.set_alpn_protocols(['http/1.1'])
            async def http1(reader, writer):
                try:
                    request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
                    counters['origin_h1'] += 1
                    if counters['origin_h1'] > 1:
                        await asyncio.sleep(0.5)
                    body = json.dumps({'transport': 'http/1.1', 'request': request.split(b' ')[1].decode()}).encode()
                    writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nAlt-Svc: h3=":{origin_port}"; ma=3600\r\nConnection: close\r\n\r\n'.encode() + body)
                    await writer.drain()
                finally:
                    writer.close()
            origin_tcp = await asyncio.start_server(http1, '127.0.0.1', 0, ssl=tls)
            origin_port = origin_tcp.sockets[0].getsockname()[1]
            configuration = QuicConfiguration(is_client=False, alpn_protocols=['h3'], max_datagram_size=1400)
            configuration.load_cert_chain(str(cert), str(key))
            class Origin(QuicConnectionProtocol):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.http = H3Connection(self._quic)
                def quic_event_received(self, event):
                    for item in self.http.handle_event(event):
                        if isinstance(item, HeadersReceived):
                            counters['origin_h3'] += 1
                            peer = self._quic._network_paths[0].addr
                            counters['origin_h3_peers'].append(list(peer))
                            body = json.dumps({'transport': 'h3', 'request': dict(item.headers)[b':path'].decode()}).encode()
                            self.http.send_headers(item.stream_id, [(b':status', b'200'), (b'content-type', b'application/json')])
                            self.http.send_data(item.stream_id, body, end_stream=True)
                            self.transmit()
            origin_udp = await quic_serve('127.0.0.1', origin_port, configuration=configuration, create_protocol=Origin)
            counts = {'turn_datagrams': 0, 'turn_channels': 0}
            original_receive = turn.datagram_received
            def receive(data, addr):
                counts['turn_datagrams'] += 1
                if data and data[0] & 0xC0 == 0x40:
                    counts['turn_channels'] += 1
                original_receive(data, addr)
            turn.datagram_received = receive
            metadata = {'turn_url': f'turn:127.0.0.1:{local_port}?transport=udp',
                        'username': 'user', 'credential': 'pass', 'local_port': local_port,
                        'remote_socks_port': remote_port, 'trap_port': trap.addr[1],
                        'origin_url': f'https://h3.test:{origin_port}/', 'spki_pin': pin, 'pid': os.getpid()}
            template = Path(__file__).with_name('webrtc_udp_fixture.html').read_text()
            html = template.replace('__FIXTURE__', json.dumps(metadata)).encode()
            async def http(reader, writer):
                try:
                    request = await reader.readuntil(b'\r\n\r\n')
                    if request.split(b' ')[1] == b'/stats':
                        body = json.dumps({**counts, **counters, 'egress_ports': sorted(egress),
                            'allocations': len(turn.allocations), 'direct_stun_packets': trap.received.qsize()}).encode()
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
                gateway.close()
                origin_udp.close()
                origin_tcp.close()
                await origin_tcp.wait_closed()
                await asyncio.gather(gateway.worker, return_exceptions=True)
                await remote.udp_close()
    loop.getaddrinfo = original_resolve


if __name__ == '__main__':
    asyncio.run(main())

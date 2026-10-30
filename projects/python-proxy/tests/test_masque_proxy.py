"""HTTP/3 CONNECT and HTTP Datagram interoperability against aioquic clients."""
import asyncio
import datetime
import ipaddress
from contextlib import asynccontextmanager

import pytest

pytest.importorskip('aioquic', reason='MASQUE qualification requires the optional aioquic dependency')
from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.events import DataReceived, DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from pproxy import server
from pproxy.masque import DatagramH3
from test_proxy_stability import echo, network, run
from test_socks5_udp import datagram, proxy
from test_turn_udp import Client, turn_server


def certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.DNSName('h3.test'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    certfile, keyfile = directory / 'cert.pem', directory / 'key.pem'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return certfile, keyfile


class H3Client(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http = DatagramH3(self._quic)
        self.events = asyncio.Queue()

    def quic_event_received(self, event):
        for item in self.http.handle_event(event):
            self.events.put_nowait(item)

    def request(self, headers):
        stream_id = self._quic.get_next_available_stream_id()
        self.http.send_headers(stream_id, headers, end_stream=False)
        self.transmit()
        return stream_id


@asynccontextmanager
async def masque_server(cert, key, remotes=(), turn_target=None):
    target = '{' + turn_target + '}' if turn_target else ''
    owner = server.proxies_by_uri(f'masque{target}://127.0.0.1:0')
    owner.quicserver.load_cert_chain(str(cert), str(key))
    listener = await owner.start_server(dict(rserver=list(remotes), urserver=list(remotes), salgorithm='fa'))
    try:
        yield listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        await asyncio.gather(listener.worker, return_exceptions=True)
        for remote in remotes:
            await remote.udp_close()
        await asyncio.sleep(0)


def client_config(cert):
    config = QuicConfiguration(is_client=True, alpn_protocols=['h3'],
        max_datagram_frame_size=65536, max_datagram_size=1400)
    config.load_verify_locations(cafile=str(cert))
    return config


@pytest.mark.parametrize('upstream', [False, True])
def test_masque_udp_datagrams_and_tcp_tunnel(tmp_path, upstream):
    cert, key = certificate(tmp_path)
    async def scenario():
        async with network() as net, datagram(echo=True) as target:
            tcp_port = await net.listen(echo)
            remote_port = await proxy(net)
            remotes = [server.proxies_by_uri(f'socks5://127.0.0.1:{remote_port}')] if upstream else []
            async with masque_server(cert, key, remotes) as port:
                async with connect('127.0.0.1', port, configuration=client_config(cert), create_protocol=H3Client) as client:
                    udp_id = client.request([(b':method', b'CONNECT'), (b':protocol', b'connect-udp'),
                        (b':scheme', b'https'), (b':authority', f'127.0.0.1:{port}'.encode()),
                        (b':path', f'/.well-known/masque/udp/127.0.0.1/{target.addr[1]}/'.encode()),
                        (b'capsule-protocol', b'?1')])
                    response = await client.events.get()
                    assert isinstance(response, HeadersReceived) and dict(response.headers)[b':status'] == b'200'
                    payload = bytes(range(256)) * 4
                    client.http.send_datagram(udp_id, b'\x00' + payload)
                    client.transmit()
                    response = await client.events.get()
                    assert isinstance(response, DatagramReceived) and response.data == b'\x00' + payload
                    tcp_id = client.request([(b':method', b'CONNECT'),
                        (b':authority', f'127.0.0.1:{tcp_port}'.encode())])
                    response = await client.events.get()
                    assert isinstance(response, HeadersReceived) and dict(response.headers)[b':status'] == b'200'
                    client.http.send_data(tcp_id, b'tcp-inside-h3', end_stream=True)
                    client.transmit()
                    data = b''
                    while True:
                        response = await client.events.get()
                        assert isinstance(response, DataReceived) and response.stream_id == tcp_id
                        data += response.data
                        if response.stream_ended:
                            break
                    assert data == b'tcp-inside-h3'
    run(scenario())


def test_turn_and_quic_share_one_udp_listener(tmp_path):
    cert, key = certificate(tmp_path)
    async def scenario():
        async with network() as net, turn_server() as (turn_endpoint, _), datagram() as udp:
            remote_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{remote_port}')
            async with masque_server(cert, key, [remote], f'127.0.0.1:{turn_endpoint[1]}') as port:
                turn = Client(udp, ('127.0.0.1', port))
                await turn.allocate()
                async with connect('127.0.0.1', port, configuration=client_config(cert), create_protocol=H3Client) as client:
                    target = await net.listen(echo)
                    stream_id = client.request([(b':method', b'CONNECT'), (b':authority', f'127.0.0.1:{target}'.encode())])
                    response = await client.events.get()
                    assert dict(response.headers)[b':status'] == b'200'
                    kind, _, _ = await turn.exchange(4, [(0x0D, b'\x00\x00\x02\x58')])
                    assert kind == 0x0104
                    client.http.send_data(stream_id, b'both-protocols', end_stream=False)
                    client.transmit()
                    response = await client.events.get()
                    assert isinstance(response, DataReceived) and response.data == b'both-protocols'
    run(scenario())

"""RFC 1928 wire tests; independent client codec and real loopback sockets."""
import asyncio
import socket
import struct
from contextlib import AsyncExitStack, asynccontextmanager

import pytest

from pproxy import proto, server
from test_proxy_stability import address, network, run


def packet(host, port, payload, kind='ipv4'):
    return b'\x00\x00\x00' + address(host, port, kind) + payload


def unpack(data):
    assert data[:3] == b'\x00\x00\x00'
    kind = data[3]
    if kind == 1:
        host, end = socket.inet_ntop(socket.AF_INET, data[4:8]), 8
    elif kind == 4:
        host, end = socket.inet_ntop(socket.AF_INET6, data[4:20]), 20
    else:
        assert kind == 3
        end = 5 + data[4]
        host = data[5:end].decode('ascii')
    return host, int.from_bytes(data[end:end + 2], 'big'), data[end + 2:]


class Datagram(asyncio.DatagramProtocol):
    def __init__(self, echo=False):
        self.echo = echo
        self.received = asyncio.Queue()
        self.closed = asyncio.get_event_loop().create_future()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.received.put_nowait((data, addr))
        if self.echo:
            self.transport.sendto(data, addr)

    def connection_lost(self, error):
        self.closed.set_result(error)

    @property
    def addr(self):
        return self.transport.get_extra_info('sockname')[:2]


@asynccontextmanager
async def datagram(host='127.0.0.1', echo=False):
    transport, protocol = await asyncio.get_event_loop().create_datagram_endpoint(
        lambda: Datagram(echo), local_addr=(host, 0))
    try:
        yield protocol
    finally:
        transport.close()
        await protocol.closed


async def proxy(net, remotes=(), tcp_remotes=(), users=(), host='127.0.0.1', block=None):
    async def handler(reader, writer):
        await server.stream_handler(
            reader, writer, unix=False, lbind=None,
            protos=[proto.HTTP(None), proto.Socks5(None)],
            rserver=list(tcp_remotes), urserver=list(remotes), cipher=None,
            sslserver=None, users=list(users), block=block)
    return await net.listen(handler, host)


async def reply(reader):
    ver, code, reserved, kind = await reader.readexactly(4)
    assert ver == 5 and reserved == 0
    if kind == 1:
        host = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
    elif kind == 4:
        host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
    else:
        assert kind == 3
        size = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(size)).decode('ascii')
    port = int.from_bytes(await reader.readexactly(2), 'big')
    return code, (host, port)


async def associate(net, port, source=('0.0.0.0', 0), host='127.0.0.1', auth=None):
    reader, writer = await asyncio.open_connection(host, port)
    net.writers.append(writer)
    writer.write(b'\x05\x01' + (b'\x02' if auth else b'\x00'))
    await writer.drain()
    assert await reader.readexactly(2) == (b'\x05\x02' if auth else b'\x05\x00')
    if auth:
        user, password = auth
        writer.write(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        await writer.drain()
        assert await reader.readexactly(2) == b'\x01\x00'
    kind = 'ipv6' if ':' in source[0] else 'ipv4'
    # The default is exactly ProxyBridge's UDP ASSOCIATE 0.0.0.0:0 request.
    writer.write(b'\x05\x03\x00' + address(*source, kind))
    await writer.drain()
    code, relay = await reply(reader)
    assert code == 0
    assert relay[1] != 0 and relay[0] not in ('0.0.0.0', '::')
    return reader, writer, relay


@pytest.mark.parametrize('kind,host', [('ipv4', '127.0.0.1'), ('ipv6', '::1'), ('domain', 'localhost')])
@pytest.mark.parametrize('payload', [b'', bytes(range(256))], ids=['empty', 'binary'])
def test_udp_codec_preserves_address_port_and_payload(kind, host, payload):
    wire = packet(host, 3478, payload, kind)
    assert proto.socks5_udp_unpack(wire) == (host, 3478, payload)
    assert unpack(proto.Socks5(None).udp_pack(host, 3478, payload)) == (host, 3478, payload)


@pytest.mark.parametrize('wire', [b'', b'\x00' * 3, b'\x00\x00\x01\x01' + b'\x00' * 6,
                                  b'\x01\x00\x00\x01' + b'\x00' * 6,
                                  b'\x00\x00\x00\x02', b'\x00\x00\x00\x03\x00\x00\x00',
                                  b'\x00\x00\x00\x03\x01\xff\x00\x01'])
def test_udp_codec_drops_malformed_or_fragmented_datagrams(wire):
    assert proto.socks5_udp_unpack(wire) is None


@pytest.mark.parametrize('kind,host', [('ipv4', '127.0.0.1'), ('ipv6', '::1'), ('domain', 'localhost')])
def test_udp_codec_drops_every_truncated_address(kind, host):
    wire = packet(host, 3478, b'', kind)
    for size in range(len(wire)):
        assert proto.socks5_udp_unpack(wire[:size]) is None


def test_proxybridge_associate_roundtrip_and_stable_multi_destination_port():
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as a, datagram(echo=True) as b:
            port = await proxy(net)
            _, _, relay = await associate(net, port)
            # Same transaction-shaped binary data travels to two STUN-like peers.
            payload = struct.pack('!HHI12s', 1, 0, 0x2112a442, b'0123456789ab')
            sources = []
            for target in (a, b, a):
                client.transport.sendto(packet(*target.addr, payload), relay)
                data, sender = await target.received.get()
                assert data == payload
                sources.append(sender)
                response, _ = await client.received.get()
                assert unpack(response) == (*target.addr, payload)
            assert len(set(sources)) == 1
    run(scenario())


@pytest.mark.parametrize('client_host,target_host,kind', [
    ('127.0.0.1', '127.0.0.1', 'ipv4'),
    ('::1', '::1', 'ipv6'),
    ('127.0.0.1', '::1', 'ipv6'),
    ('::1', '127.0.0.1', 'ipv4'),
    ('127.0.0.1', '127.0.0.1', 'domain'),
])
def test_udp_authenticated_address_families_and_empty_payload(client_host, target_host, kind):
    async def scenario():
        async with network() as net, datagram(client_host) as client, datagram(target_host, echo=True) as target:
            port = await proxy(net, users=[b'user:pass'], host=client_host)
            _, _, relay = await associate(net, port, source=client.addr, host=client_host, auth=(b'user', b'pass'))
            for payload in (b'', b'\x16\xfe\xfd' + bytes(range(256)) * 4):
                client.transport.sendto(packet(*target.addr, payload, kind), relay)
                response, _ = await client.received.get()
                assert unpack(response) == (*target.addr, payload)
    run(scenario())


@pytest.mark.parametrize('upstream_auth', [False, True])
def test_udp_socks5_upstream_chain_preserves_multi_destination_port(upstream_auth):
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as a, datagram(echo=True) as b:
            users = [b'user:pass'] if upstream_auth else []
            exit_port = await proxy(net, users=users)
            suffix = '#user:pass' if upstream_auth else ''
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}{suffix}')
            entry = await proxy(net, remotes=[remote], tcp_remotes=[remote])
            _, control, relay = await associate(net, entry)
            observed = []
            for target in (a, b, a):
                payload = bytes(range(256)) * 4
                client.transport.sendto(packet(*target.addr, payload), relay)
                received, addr = await target.received.get()
                observed.append(addr)
                response, _ = await client.received.get()
                assert received == payload
                assert unpack(response) == (*target.addr, payload)
            assert len(set(observed)) == 1
            control.close()
            await control.wait_closed()
            for _ in range(100):
                if not net.tasks:
                    break
                await asyncio.sleep(0.005)
            assert not net.tasks, 'downstream and upstream control connections must close together'
    run(scenario())


def test_udp_control_close_releases_relay_and_stops_forwarding():
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as target:
            port = await proxy(net)
            _, control, relay = await associate(net, port)
            client.transport.sendto(packet(*target.addr, b'before'), relay)
            await client.received.get()
            _, egress = await target.received.get()
            control.close()
            await control.wait_closed()
            for _ in range(100):
                if not net.tasks:
                    break
                await asyncio.sleep(0.005)
            assert not net.tasks
            # Both listening ports become bindable again; no relay survives EOF.
            for addr in (relay, egress):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.bind(addr)
            client.transport.sendto(packet(*target.addr, b'after'), relay)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(target.received.get(), 0.05)
    run(scenario())


def test_udp_source_port_pinning_fragments_and_malformed_packets():
    async def scenario():
        async with network() as net, datagram() as client, datagram() as wrong, datagram(echo=True) as target:
            port = await proxy(net)
            _, _, relay = await associate(net, port, source=client.addr)
            valid = packet(*target.addr, b'valid')
            wrong.transport.sendto(valid, relay)
            for wire in (b'bad', valid[:7], b'\x00\x00\x01' + valid[3:]):
                client.transport.sendto(wire, relay)
            client.transport.sendto(valid, relay)
            data, _ = await target.received.get()
            assert data == b'valid'
            response, _ = await client.received.get()
            assert unpack(response) == (*target.addr, b'valid')
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(target.received.get(), 0.05)
            assert wrong.received.empty()
    run(scenario())


def test_udp_wildcard_source_pins_first_valid_sender():
    async def scenario():
        async with network() as net, datagram() as client, datagram() as wrong, datagram(echo=True) as target:
            port = await proxy(net)
            _, _, relay = await associate(net, port)
            wrong.transport.sendto(b'invalid', relay)
            client.transport.sendto(packet(*target.addr, b'first'), relay)
            await client.received.get()
            await target.received.get()
            wrong.transport.sendto(packet(*target.addr, b'wrong'), relay)
            client.transport.sendto(packet(*target.addr, b'second'), relay)
            data, _ = await target.received.get()
            assert data == b'second'
            await client.received.get()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(target.received.get(), 0.05)
    run(scenario())


@pytest.mark.parametrize('wire,expected', [
    (b'\x05\x02\x00' + address('0.0.0.0', 0), 7),
    (b'\x05\x03\x00\x02', 8),
    (b'\x05\x03\x00' + address('192.0.2.1', 0, 'ipv4'), 2),
])
def test_udp_rejects_unsupported_requests_with_socks_reply(wire, expected):
    async def scenario():
        async with network() as net:
            port = await proxy(net)
            reader, writer = await net.tcp(port)
            writer.write(b'\x05\x01\x00')
            await writer.drain()
            assert await reader.readexactly(2) == b'\x05\x00'
            writer.write(wire)
            await writer.drain()
            code, _ = await reply(reader)
            assert code == expected
            assert await reader.read() == b''
    run(scenario())


def test_tcp_only_upstream_does_not_enable_direct_udp():
    async def scenario():
        async with network() as net:
            remote = server.proxies_by_uri('socks5://127.0.0.1:1')
            port = await proxy(net, tcp_remotes=[remote])
            reader, writer = await net.tcp(port)
            writer.write(b'\x05\x01\x00')
            await writer.drain()
            assert await reader.readexactly(2) == b'\x05\x00'
            writer.write(bytes.fromhex('05 03 00 01 00 00 00 00 00 00'))
            await writer.drain()
            assert (await reply(reader))[0] == 7
            assert await reader.read() == b''
    run(scenario())


def test_udp_block_rule_and_unavailable_route_never_send_direct():
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as target:
            remote = server.proxies_by_uri('socks5://127.0.0.1:1')
            remote.alive = False
            for options in ({'block': lambda host: True}, {'remotes': [remote]}):
                port = await proxy(net, **options)
                _, _, relay = await associate(net, port)
                client.transport.sendto(packet(*target.addr, b'blocked'), relay)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(target.received.get(), 0.05)
    run(scenario())


def test_udp_client_api_uses_standard_association_and_closes_it():
    async def scenario():
        async with network() as net, datagram(echo=True) as target:
            exit_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}')
            answers = asyncio.Queue()
            try:
                await remote.udp_sendto(*target.addr, b'api', answers.put_nowait)
                assert await answers.get() == b'api'
                assert remote.connections == 1
            finally:
                await remote.udp_close()
            assert remote.connections == 0
            assert not remote.udp_associations
    run(scenario())


def test_udp_listener_to_standard_socks_upstream_keeps_reply_source():
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as a, datagram(echo=True) as b:
            exit_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}')
            listener = server.proxies_by_uri('socks5://127.0.0.1:0')
            transport, _ = await listener.udp_start_server(dict(urserver=[remote], block=None, salgorithm='fa'))
            try:
                for target in (a, b, a):
                    client.transport.sendto(packet(*target.addr, b'legacy-listener'), transport.get_extra_info('sockname'))
                    response, _ = await client.received.get()
                    assert unpack(response) == (*target.addr, b'legacy-listener')
                assert len(remote.udp_associations) == 1
            finally:
                transport.close()
                await remote.udp_close()
    run(scenario())


@pytest.mark.parametrize('relay_host,reply_host,kind', [
    ('127.0.0.1', '0.0.0.0', 'ipv4'),
    ('127.0.0.1', '127.0.0.1', 'domain'),
    ('::1', '::1', 'ipv6'),
])
def test_independent_upstream_reply_endpoint_and_control_eof(relay_host, reply_host, kind):
    async def scenario():
        async with network() as net, datagram() as client, datagram(relay_host) as upstream:
            close_control = asyncio.Event()
            async def control(reader, writer):
                assert await reader.readexactly(3) == bytes.fromhex('05 01 00')
                writer.write(bytes.fromhex('05 00'))
                await writer.drain()
                assert await reader.readexactly(10) == bytes.fromhex('05 03 00 01 00 00 00 00 00 00')
                writer.write(b'\x05\x00\x00' + address(reply_host, upstream.addr[1], kind))
                await writer.drain()
                await close_control.wait()
                writer.close()
            exit_port = await net.listen(control)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}')
            entry = await proxy(net, remotes=[remote])
            reader, _, relay = await associate(net, entry)
            client.transport.sendto(packet('192.0.2.10', 3478, b'outbound'), relay)
            request_wire, sender = await upstream.received.get()
            assert unpack(request_wire) == ('192.0.2.10', 3478, b'outbound')
            # The response source must come from the upstream's UDP header,
            # including a peer-reflexive address different from the request.
            upstream.transport.sendto(packet('192.0.2.20', 50000, b'inbound'), sender)
            response_wire, _ = await client.received.get()
            assert unpack(response_wire) == ('192.0.2.20', 50000, b'inbound')
            close_control.set()
            assert await reader.read() == b''
            assert remote.connections == 0
    run(scenario())


def test_domain_destination_resolves_and_relays_datagrams():
    async def scenario():
        addresses = await asyncio.get_event_loop().getaddrinfo('localhost', 0, type=socket.SOCK_DGRAM)
        target_host = addresses[0][4][0]
        async with network() as net, datagram() as client, datagram(target_host, echo=True) as target:
            port = await proxy(net)
            _, _, relay = await associate(net, port)
            client.transport.sendto(packet('localhost', target.addr[1], b'dns', 'domain'), relay)
            response, _ = await client.received.get()
            assert unpack(response) == (*target.addr, b'dns')
    run(scenario())


def test_udp_associations_are_isolated_beyond_legacy_udp_limit():
    async def scenario():
        async with network() as net, datagram(echo=True) as target, AsyncExitStack() as stack:
            port = await proxy(net)
            clients = []
            for i in range(server.UDP_LIMIT + 2):
                client = await stack.enter_async_context(datagram())
                _, _, relay = await associate(net, port)
                clients.append((client, relay))
            for turn in range(2):
                for i, (client, relay) in enumerate(clients):
                    client.transport.sendto(packet(*target.addr, struct.pack('!II', turn, i)), relay)
                for i, (client, _) in enumerate(clients):
                    response, _ = await client.received.get()
                    assert unpack(response) == (*target.addr, struct.pack('!II', turn, i))
    run(scenario())


def test_udp_stun_binding_reports_same_mapping_for_multiple_servers():
    async def scenario():
        async with network() as net, datagram() as client, datagram() as a, datagram() as b:
            port = await proxy(net)
            _, _, relay = await associate(net, port)
            transaction = b'0123456789ab'
            request_wire = struct.pack('!HHI12s', 1, 0, 0x2112a442, transaction)
            mappings = []
            for stun in (a, b):
                client.transport.sendto(packet(*stun.addr, request_wire), relay)
                request_data, sender = await stun.received.get()
                assert request_data == request_wire
                mapped_ip = int.from_bytes(socket.inet_aton(sender[0]), 'big') ^ 0x2112a442
                attribute = struct.pack('!HHBBHI', 0x0020, 8, 0, 1, sender[1] ^ 0x2112, mapped_ip)
                response = struct.pack('!HHI12s', 0x0101, len(attribute), 0x2112a442, transaction) + attribute
                stun.transport.sendto(response, sender)
                result, _ = await client.received.get()
                source_host, source_port, data = unpack(result)
                assert (source_host, source_port) == stun.addr
                assert struct.unpack('!HHI12s', data[:20]) == (0x0101, 12, 0x2112a442, transaction)
                kind, size, reserved, family, xport, xip = struct.unpack('!HHBBHI', data[20:])
                assert (kind, size, reserved, family) == (0x0020, 8, 0, 1)
                mappings.append((xip ^ 0x2112a442, xport ^ 0x2112))
            assert mappings[0] == mappings[1]
    run(scenario())


def test_udp_client_idle_timeout_counts_incoming_traffic(monkeypatch):
    monkeypatch.setattr(server, 'UDP_ASSOCIATION_IDLE', 0.2)
    async def scenario():
        async with network() as net, datagram(echo=True) as target:
            exit_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}')
            answers = asyncio.Queue()
            try:
                await remote.udp_sendto(*target.addr, b'start', answers.put_nowait)
                assert await answers.get() == b'start'
                _, sender = await target.received.get()
                for _ in range(6):
                    await asyncio.sleep(0.05)
                    target.transport.sendto(b'incoming', sender)
                    assert await answers.get() == b'incoming'
                assert remote.connections == 1
                for _ in range(100):
                    if remote.connections == 0:
                        break
                    await asyncio.sleep(0.01)
                assert remote.connections == 0
                assert not remote.udp_associations
            finally:
                await remote.udp_close()
    run(scenario())


@pytest.mark.parametrize('failure', ['reject', 'timeout'])
def test_udp_upstream_failure_closes_control_without_direct_fallback(failure, monkeypatch):
    monkeypatch.setattr(server, 'SOCKET_TIMEOUT', 0.1)
    async def scenario():
        async with network() as net, datagram() as client, datagram(echo=True) as target:
            closed = asyncio.Event()
            async def upstream(reader, writer):
                assert await reader.readexactly(3) == bytes.fromhex('05 01 00')
                if failure == 'reject':
                    writer.write(bytes.fromhex('05 00'))
                    await writer.drain()
                    assert await reader.readexactly(10) == bytes.fromhex('05 03 00 01 00 00 00 00 00 00')
                    writer.write(bytes.fromhex('05 07 00 01 00 00 00 00 00 00'))
                    await writer.drain()
                assert await reader.read() == b''
                writer.close()
                closed.set()
            exit_port = await net.listen(upstream)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{exit_port}')
            port = await proxy(net, remotes=[remote])
            _, _, relay = await associate(net, port)
            client.transport.sendto(packet(*target.addr, b'never-direct'), relay)
            await closed.wait()
            assert remote.connections == 0
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(target.received.get(), 0.05)
    run(scenario())

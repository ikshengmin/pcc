"""Real UDP TURN client/peer tests, including the local SOCKS5 tunnel route."""
import asyncio
import hashlib
import hmac
import re
import secrets
import signal
import socket
import struct
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from pproxy import server
from test_proxy_stability import network, run
from test_socks5_udp import datagram, proxy

COOKIE = 0x2112A442


def attr(kind, value):
    return struct.pack('!HH', kind, len(value)) + value + b'\0' * (-len(value) % 4)


def wire(kind, tid, attrs=(), key=None):
    body = b''.join(attr(k, v) for k, v in attrs)
    header = struct.pack('!HHI12s', kind, len(body) + (24 if key else 0), COOKIE, tid)
    data = header + body
    return data + attr(8, hmac.new(key, data, hashlib.sha1).digest()) if key else data


def decode(data):
    kind, length, cookie, tid = struct.unpack('!HHI12s', data[:20])
    assert cookie == COOKIE and length == len(data) - 20
    values, pos = {}, 20
    while pos < len(data):
        atype, size = struct.unpack('!HH', data[pos:pos + 4])
        values[atype] = data[pos + 4:pos + 4 + size]
        pos += 4 + size + (-size % 4)
    return kind, tid, values


def xor_addr(addr, tid):
    family = socket.AF_INET6 if ':' in addr[0] else socket.AF_INET
    raw = socket.inet_pton(family, addr[0])
    mask = struct.pack('!I', COOKIE) + tid
    return bytes([0, 2 if family == socket.AF_INET6 else 1]) + struct.pack('!H', addr[1] ^ 0x2112) + bytes(a ^ b for a, b in zip(raw, mask))


def unxor_addr(value, tid):
    mask = struct.pack('!I', COOKIE) + tid
    raw = bytes(a ^ b for a, b in zip(value[4:], mask))
    host = socket.inet_ntop(socket.AF_INET6 if value[1] == 2 else socket.AF_INET, raw)
    return host, int.from_bytes(value[2:4], 'big') ^ 0x2112


class Client:
    def __init__(self, udp, endpoint, password=b'pass'):
        self.udp, self.endpoint, self.password = udp, endpoint, password
        self.realm = self.nonce = self.key = None

    async def exchange(self, kind, attrs=(), authenticated=True, tid=None):
        tid = tid or secrets.token_bytes(12)
        attrs = list(attrs(tid) if callable(attrs) else attrs)
        if authenticated:
            attrs += [(6, b'user'), (0x14, self.realm), (0x15, self.nonce)]
        request = wire(kind, tid, attrs, self.key if authenticated else None)
        self.udp.transport.sendto(request, self.endpoint)
        data, _ = await self.udp.received.get()
        result, response_tid, values = decode(data)
        assert response_tid == tid
        if 8 in values:
            # Responses carry MESSAGE-INTEGRITY as their final attribute.
            assert hmac.compare_digest(values[8], hmac.new(self.key, data[:-24], hashlib.sha1).digest())
        return result, response_tid, values

    async def allocate(self, family=1):
        attrs = [(0x19, b'\x11\x00\x00\x00'), (0x17, bytes([family, 0, 0, 0]))]
        await self.authenticate(attrs)
        kind, tid, values = await self.exchange(3, attrs)
        assert kind == 0x0103, (kind, values)
        return unxor_addr(values[0x16], tid)

    async def authenticate(self, attrs=()):
        kind, _, values = await self.exchange(3, attrs, authenticated=False)
        assert kind == 0x0113
        assert values[9][2:4] == bytes([4, 1])
        self.realm, self.nonce = values[0x14], values[0x15]
        self.key = hashlib.md5(b'user:' + self.realm + b':' + self.password).digest()


@asynccontextmanager
async def turn_server(host='127.0.0.1', **args):
    uri = f'turn://[{host}]:0#user:pass' if ':' in host else f'turn://{host}:0#user:pass'
    listener = server.proxies_by_uri(uri)
    transport, protocol = await listener.udp_start_server(dict(turn_allow_loopback=True, **args))
    try:
        yield transport.get_extra_info('sockname')[:2], protocol
    finally:
        transport.close()
        await asyncio.sleep(0)
        if protocol.worker:
            await asyncio.gather(protocol.worker, return_exceptions=True)
        assert not protocol.allocations


@pytest.mark.parametrize('host,family', [('127.0.0.1', 1), ('::1', 2)])
def test_turn_authenticated_permission_channel_and_refresh(host, family):
    async def scenario():
        async with turn_server(host) as (endpoint, protocol), datagram(host) as udp, datagram(host, echo=True) as peer:
            client = Client(udp, endpoint)
            relay = await client.allocate(family)
            kind, _, _ = await client.exchange(8, lambda tid: [(0x12, xor_addr(peer.addr, tid))])
            assert kind == 0x0108
            tid, payload = secrets.token_bytes(12), bytes(range(256))
            udp.transport.sendto(wire(0x16, tid, [(0x12, xor_addr(peer.addr, tid)), (0x13, payload)]), endpoint)
            received, source = await peer.received.get()
            assert received == payload and source[:2] == relay
            data, _ = await udp.received.get()
            kind, tid, values = decode(data)
            assert kind == 0x17 and values[0x13] == payload
            assert unxor_addr(values[0x12], tid) == peer.addr
            kind, _, _ = await client.exchange(9, lambda tid: [(0x0C, bytes.fromhex('40010000')), (0x12, xor_addr(peer.addr, tid))])
            assert kind == 0x0109
            channel_packet = struct.pack('!HH', 0x4001, len(payload)) + payload
            udp.transport.sendto(channel_packet, endpoint)
            data, _ = await udp.received.get()
            assert data == channel_packet
            kind, _, _ = await client.exchange(4, [(0x0D, b'\x00' * 4)])
            assert kind == 0x0104 and not protocol.allocations
    run(scenario())


def test_turn_over_local_udp_tunnel_and_remote_socks():
    async def scenario():
        async with network() as net, turn_server() as (endpoint, _), datagram() as udp, datagram(echo=True) as peer:
            remote_port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{remote_port}')
            local_port = await proxy(net, tcp_remotes=[remote], remotes=[remote])
            listener = server.proxies_by_uri(f'tunnel{{127.0.0.1:{endpoint[1]}}}://127.0.0.1:{local_port}')
            transport, _ = await listener.udp_start_server(dict(urserver=[remote], block=None, salgorithm='fa'))
            try:
                client = Client(udp, ('127.0.0.1', local_port))
                relay = await client.allocate()
                kind, _, _ = await client.exchange(9, lambda tid: [(0x0C, bytes.fromhex('40020000')), (0x12, xor_addr(peer.addr, tid))])
                assert kind == 0x0109
                payload = b'UDP stays UDP through local tunnel and remote SOCKS5'
                channel_packet = struct.pack('!HH', 0x4002, len(payload)) + payload
                udp.transport.sendto(channel_packet, client.endpoint)
                received, source = await peer.received.get()
                assert received == payload and source[:2] == relay
                result, _ = await udp.received.get()
                assert result == channel_packet
            finally:
                transport.close()
                await remote.udp_close()
    run(scenario())


def test_turn_retransmitted_allocate_reuses_the_same_relay():
    async def scenario():
        async with turn_server() as (endpoint, protocol), datagram() as udp:
            client = Client(udp, endpoint)
            await client.authenticate()
            tid = secrets.token_bytes(12)
            attrs = [(0x19, bytes.fromhex('11000000'))]
            first = await client.exchange(3, attrs, tid=tid)
            second = await client.exchange(3, attrs, tid=tid)
            assert first == second and first[0] == 0x0103
            assert len(protocol.allocations) == 1
    run(scenario())


@pytest.mark.parametrize('case,expected', [('password', 401), ('nonce', 438), ('transport', 442), ('family', 440), ('unknown', 420)])
def test_turn_rejects_invalid_authenticated_requests(case, expected):
    async def scenario():
        async with turn_server() as (endpoint, protocol), datagram() as udp:
            client = Client(udp, endpoint, password=b'wrong' if case == 'password' else b'pass')
            await client.authenticate()
            attrs = [(0x19, bytes.fromhex('06000000') if case == 'transport' else bytes.fromhex('11000000'))]
            if case == 'nonce':
                client.nonce = b'invalid'
            if case == 'family':
                attrs.append((0x17, bytes.fromhex('02000000')))
            if case == 'unknown':
                attrs.append((0x7777, b'\0' * 4))
            kind, _, values = await client.exchange(3, attrs)
            assert kind == 0x0113
            assert values[9][2] * 100 + values[9][3] == expected
            assert not protocol.allocations
    run(scenario())


def test_turn_data_requires_peer_permission_and_permission_expires():
    async def scenario():
        async with turn_server() as (endpoint, protocol), datagram() as udp, datagram(echo=True) as peer:
            client = Client(udp, endpoint)
            await client.allocate()
            tid = secrets.token_bytes(12)
            packet = wire(0x16, tid, [(0x12, xor_addr(peer.addr, tid)), (0x13, b'blocked')])
            udp.transport.sendto(packet, endpoint)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(peer.received.get(), 0.05)
            await client.exchange(8, lambda tid: [(0x12, xor_addr(peer.addr, tid))])
            udp.transport.sendto(packet, endpoint)
            assert (await peer.received.get())[0] == b'blocked'
            await udp.received.get()
            allocation = next(iter(protocol.allocations.values()))
            allocation.permissions[peer.addr[0]] = 0
            udp.transport.sendto(packet, endpoint)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(peer.received.get(), 0.05)
    run(scenario())


def test_turn_quota_and_allocation_expiry():
    async def scenario():
        async with turn_server(turn_max_allocations=1) as (endpoint, protocol), datagram() as a, datagram() as b:
            first, second = Client(a, endpoint), Client(b, endpoint)
            await first.allocate()
            await second.authenticate()
            kind, _, values = await second.exchange(3, [(0x19, bytes.fromhex('11000000'))])
            assert kind == 0x0113 and values[9][2:4] == bytes([4, 86])
            next(iter(protocol.allocations.values())).refresh(0.05)
            await asyncio.sleep(0.1)
            assert not protocol.allocations
            await second.allocate()
            assert len(protocol.allocations) == 1
    run(scenario())


def test_cli_turn_listener_announces_port_and_relays_udp():
    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-u', '-m', 'pproxy', '-ul', 'turn://127.0.0.1:0#user:pass',
            '--turn-allow-loopback', cwd=Path(__file__).resolve().parents[1],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            match = None
            for _ in range(5):
                line = await asyncio.wait_for(process.stdout.readline(), 3)
                match = re.search(rb'127\.0\.0\.1:(\d+)', line)
                if match:
                    break
            assert match and int(match[1]) > 0, 'UDP listener must announce its actual bound port'
            async with datagram() as udp, datagram(echo=True) as peer:
                client = Client(udp, ('127.0.0.1', int(match[1])))
                await client.allocate()
                await client.exchange(9, lambda tid: [(0x0C, bytes.fromhex('40010000')), (0x12, xor_addr(peer.addr, tid))])
                payload = b'cli-udp'
                packet = struct.pack('!HH', 0x4001, len(payload)) + payload
                udp.transport.sendto(packet, client.endpoint)
                assert (await udp.received.get())[0] == packet
            process.send_signal(signal.SIGINT)
            _, stderr = await asyncio.wait_for(process.communicate(), 3)
            assert process.returncode == 0, stderr.decode()
            assert b'Task was destroyed' not in stderr and b'never awaited' not in stderr
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    run(scenario())


def test_udp_tunnel_does_not_bypass_unavailable_socks_upstream():
    async def scenario():
        async with datagram() as udp, datagram(echo=True) as target:
            remote = server.proxies_by_uri('socks5://127.0.0.1:1')
            remote.alive = False
            listener = server.proxies_by_uri(f'tunnel{{127.0.0.1:{target.addr[1]}}}://127.0.0.1:0')
            transport, _ = await listener.udp_start_server(dict(urserver=[remote], block=None, salgorithm='fa'))
            try:
                udp.transport.sendto(b'no-direct', transport.get_extra_info('sockname'))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(target.received.get(), 0.1)
            finally:
                transport.close()
    run(scenario())


def test_turn_tunnel_can_keep_client_association_past_default_idle(monkeypatch):
    monkeypatch.setattr(server, 'UDP_ASSOCIATION_IDLE', 0.05)
    async def scenario():
        async with network() as net, datagram(echo=True) as target:
            port = await proxy(net)
            remote = server.proxies_by_uri(f'socks5://127.0.0.1:{port}')
            remote.udp_idle_timeout = 0.3
            answers = asyncio.Queue()
            try:
                await remote.udp_sendto(*target.addr, b'before', answers.put_nowait, local_addr='turn')
                assert await answers.get() == b'before'
                _, first_source = await target.received.get()
                await asyncio.sleep(0.12)
                await remote.udp_sendto(*target.addr, b'after', answers.put_nowait, local_addr='turn')
                assert await answers.get() == b'after'
                _, second_source = await target.received.get()
                assert first_source == second_source
            finally:
                await remote.udp_close()
    run(scenario())

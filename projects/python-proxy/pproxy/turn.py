"""Authenticated TURN/UDP relay (RFC 8656), with no TCP media fallback."""
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import socket
import struct
import time
import zlib

COOKIE = 0x2112A442
HEADER = struct.Struct('!HHI12s')
BINDING, ALLOCATE, REFRESH = 1, 3, 4
SEND, DATA, PERMISSION, CHANNEL_BIND = 6, 7, 8, 9
USERNAME, INTEGRITY, ERROR, UNKNOWN = 0x0006, 0x0008, 0x0009, 0x000A
CHANNEL, LIFETIME, PEER, PAYLOAD = 0x000C, 0x000D, 0x0012, 0x0013
REALM, NONCE, RELAYED, FAMILY = 0x0014, 0x0015, 0x0016, 0x0017
TRANSPORT, MAPPED, FINGERPRINT = 0x0019, 0x0020, 0x8028
KNOWN = {USERNAME, INTEGRITY, CHANNEL, LIFETIME, PEER, PAYLOAD,
         REALM, NONCE, FAMILY, TRANSPORT}
REASONS = {400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
           420: 'Unknown Attribute', 437: 'Allocation Mismatch',
           438: 'Stale Nonce', 440: 'Address Family not Supported',
           441: 'Wrong Credentials', 442: 'Unsupported Transport Protocol', 443: 'Peer Address Family Mismatch',
           486: 'Allocation Quota Reached', 508: 'Insufficient Capacity'}


def attribute(kind, value):
    return struct.pack('!HH', kind, len(value)) + value + b'\x00' * (-len(value) % 4)


def message(kind, transaction, attrs=(), key=None):
    body = b''.join(attribute(k, v) for k, v in attrs)
    wire = HEADER.pack(kind, len(body) + (24 if key else 0), COOKIE, transaction) + body
    if key:
        wire += attribute(INTEGRITY, hmac.new(key, wire, hashlib.sha1).digest())
    return wire


def parse(wire):
    if len(wire) < HEADER.size:
        raise ValueError('Short STUN packet')
    kind, size, cookie, transaction = HEADER.unpack_from(wire)
    if kind & 0xC000 or cookie != COOKIE or size % 4 or size + HEADER.size != len(wire):
        raise ValueError('Invalid STUN header')
    attrs = []
    offset = HEADER.size
    while offset < len(wire):
        if offset + 4 > len(wire):
            raise ValueError('Truncated STUN attribute')
        code, length = struct.unpack_from('!HH', wire, offset)
        end = offset + 4 + length
        if end > len(wire):
            raise ValueError('Truncated STUN value')
        attrs.append((code, wire[offset + 4:end], offset))
        offset = end + (-length % 4)
    if offset != len(wire):
        raise ValueError('Truncated STUN padding')
    fingerprints = [a for a in attrs if a[0] == FINGERPRINT]
    if fingerprints:
        if len(fingerprints) != 1 or attrs[-1][0] != FINGERPRINT:
            raise ValueError('Invalid STUN fingerprint placement')
        _, value, pos = fingerprints[0]
        if value != struct.pack('!I', zlib.crc32(wire[:pos]) ^ 0x5354554E):
            raise ValueError('Invalid STUN fingerprint')
    return kind, transaction, attrs


def xor_address(addr, transaction):
    ip = ipaddress.ip_address(addr[0])
    mask = struct.pack('!I', COOKIE) + transaction
    return struct.pack('!BBH', 0, 1 if ip.version == 4 else 2, addr[1] ^ (COOKIE >> 16)) + bytes(
        a ^ b for a, b in zip(ip.packed, mask))


def read_address(value, transaction):
    if len(value) not in (8, 20) or value[0] != 0:
        raise ValueError('Invalid TURN address')
    family = value[1]
    if (family, len(value)) not in ((1, 8), (2, 20)):
        raise ValueError('Invalid TURN address family')
    mask = struct.pack('!I', COOKIE) + transaction
    ip = ipaddress.ip_address(bytes(a ^ b for a, b in zip(value[4:], mask)))
    return str(ip), int.from_bytes(value[2:4], 'big') ^ (COOKIE >> 16)


class TurnError(Exception):
    def __init__(self, code, attrs=(), key=None):
        self.code, self.attrs, self.key = code, attrs, key


class Allocation(asyncio.DatagramProtocol):
    def __init__(self, owner, client, username):
        self.owner, self.client, self.username = owner, client, username
        self.transport = None
        self.permissions = {}
        self.channels = {}
        self.timer = None
        self.closed = False

    def connection_made(self, transport):
        self.transport = transport

    def refresh(self, lifetime):
        if self.timer:
            self.timer.cancel()
        if lifetime:
            self.timer = asyncio.get_event_loop().call_later(lifetime, self.close)
        else:
            self.close()

    def send(self, data, peer):
        if not self.closed and self.permissions.get(peer[0], 0) > time.monotonic():
            self.transport.sendto(data, peer)

    def datagram_received(self, data, addr):
        if self.closed or self.permissions.get(addr[0], 0) <= time.monotonic():
            return
        peer = addr[:2]
        now = time.monotonic()
        channel = next((c for c, (p, expires) in self.channels.items() if p == peer and expires > now), None)
        if channel is not None:
            wire = struct.pack('!HH', channel, len(data)) + data
        else:
            transaction = secrets.token_bytes(12)
            wire = message(DATA | 0x10, transaction, [(PEER, xor_address(peer, transaction)), (PAYLOAD, data)])
        try:
            self.owner.transport.sendto(wire, self.client)
        except OSError:
            pass

    def error_received(self, error):
        pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer:
            self.timer.cancel()
        if self.transport:
            self.transport.close()
        if self.owner.allocations.get(self.client) is self:
            del self.owner.allocations[self.client]


class TurnServer(asyncio.DatagramProtocol):
    def __init__(self, users, relay_address, external_address=None, realm='pproxy',
                 min_port=0, max_port=0, max_allocations=128, allow_loopback=False):
        if not users:
            raise ValueError('TURN requires #username:password credentials')
        self.users = {}
        for user in users:
            name, separator, password = user.partition(b':')
            if not separator or not name or not password:
                raise ValueError('TURN requires nonempty username:password credentials')
            self.users[name] = password
        self.relay_address = str(ipaddress.ip_address(relay_address))
        self.external_address = str(ipaddress.ip_address(external_address or relay_address))
        if ipaddress.ip_address(self.relay_address).is_unspecified:
            raise ValueError('TURN requires a concrete --turn-relay-address')
        if ipaddress.ip_address(self.external_address).is_unspecified:
            raise ValueError('TURN requires a concrete external address')
        self.family = ipaddress.ip_address(self.relay_address).version
        if ipaddress.ip_address(self.external_address).version != self.family:
            raise ValueError('TURN relay and external address families must match')
        if not ((min_port == max_port == 0) or (1024 <= min_port <= max_port <= 65535)):
            raise ValueError('Invalid TURN relay port range')
        if max_allocations < 1:
            raise ValueError('Invalid TURN allocation quota')
        self.min_port, self.max_port = min_port, max_port
        self.max_allocations, self.allow_loopback = max_allocations, allow_loopback
        self.realm = realm.encode('utf8')
        self.secret = secrets.token_bytes(32)
        self.allocations = {}
        self.responses = {}
        self.queue = asyncio.Queue(maxsize=256)
        self.transport = self.worker = None
        self.closed = False

    def connection_made(self, transport):
        self.transport = transport
        self.worker = asyncio.ensure_future(self.process_requests())

    def connection_lost(self, error):
        self.closed = True
        if self.worker:
            self.worker.cancel()
        for allocation in list(self.allocations.values()):
            allocation.close()

    def error_received(self, error):
        pass

    def datagram_received(self, wire, client):
        if self.closed or len(wire) < 4:
            return
        allocation = self.allocations.get(client)
        channel, size = struct.unpack_from('!HH', wire)
        if 0x4000 <= channel <= 0x7FFF:
            if allocation is not None and len(wire) >= size + 4:
                bound = allocation.channels.get(channel)
                if bound is not None and bound[1] > time.monotonic():
                    allocation.send(wire[4:4 + size], bound[0])
            return
        try:
            parsed = parse(wire)
            if parsed[0] == SEND | 0x10:
                if allocation is not None:
                    attrs = self.values(parsed[2])
                    peer = read_address(self.one(attrs, PEER), parsed[1])
                    allocation.send(self.one(attrs, PAYLOAD), peer)
                return
            if parsed[0] & 0x0110:
                return
            self.queue.put_nowait((wire, client, parsed))
        except (ValueError, TurnError, asyncio.QueueFull, OSError):
            pass

    @staticmethod
    def values(attrs):
        values = {}
        for code, value, _ in attrs:
            values.setdefault(code, []).append(value)
        return values

    @staticmethod
    def one(attrs, code, default=None):
        values = attrs.get(code, [])
        if len(values) > 1 or (not values and default is None):
            raise TurnError(400)
        return values[0] if values else default

    def nonce(self, client):
        stamp = str(int(time.time())).encode()
        signature = hmac.new(self.secret, stamp + b':' + client[0].encode(), hashlib.sha256).digest()[:16]
        return base64.b64encode(stamp + b':' + signature)

    def nonce_valid(self, value, client):
        try:
            stamp, signature = base64.b64decode(value, validate=True).split(b':', 1)
            if not 0 <= time.time() - int(stamp) <= 600:
                return False
            expected = hmac.new(self.secret, stamp + b':' + client[0].encode(), hashlib.sha256).digest()[:16]
            return hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    def authenticate(self, wire, client, attrs):
        signatures = [a for a in attrs if a[0] == INTEGRITY]
        if not signatures:
            raise TurnError(401)
        if len(signatures) != 1 or len(signatures[0][1]) != 20:
            raise TurnError(400)
        _, signature, pos = signatures[0]
        # Attributes following MESSAGE-INTEGRITY cannot influence processing.
        values = self.values(a for a in attrs if a[2] < pos)
        username = self.one(values, USERNAME)
        if self.one(values, REALM) != self.realm or username not in self.users:
            raise TurnError(401)
        key = hashlib.md5(username + b':' + self.realm + b':' + self.users[username]).digest()
        signed = wire[:2] + struct.pack('!H', pos - HEADER.size + 24) + wire[4:pos]
        if not hmac.compare_digest(signature, hmac.new(key, signed, hashlib.sha1).digest()):
            raise TurnError(401)
        if not self.nonce_valid(self.one(values, NONCE), client):
            raise TurnError(438, key=key)
        unknown = sorted({a for a in values if a < 0x8000 and a not in KNOWN})
        if unknown:
            raise TurnError(420, [(UNKNOWN, b''.join(struct.pack('!H', a) for a in unknown))], key)
        return username, key, values

    def peer(self, value, transaction):
        peer = read_address(value, transaction)
        ip = ipaddress.ip_address(peer[0])
        if ip.version != self.family:
            raise TurnError(443)
        if not peer[1] or ip.is_unspecified or ip.is_multicast or (ip.is_loopback and not self.allow_loopback):
            raise TurnError(403)
        return peer

    def lifetime(self, attrs):
        value = self.one(attrs, LIFETIME, struct.pack('!I', 600))
        if len(value) != 4:
            raise TurnError(400)
        return min(int.from_bytes(value, 'big'), 3600)

    async def allocate(self, client, username):
        allocation = Allocation(self, client, username)
        ports = [0] if not self.min_port else range(self.min_port, self.max_port + 1)
        for port in ports:
            try:
                await asyncio.get_event_loop().create_datagram_endpoint(
                    lambda: allocation, local_addr=(self.relay_address, port),
                    family=socket.AF_INET if self.family == 4 else socket.AF_INET6)
                self.allocations[client] = allocation
                return allocation
            except OSError:
                continue
        raise TurnError(508)

    async def process(self, kind, transaction, client, username, attrs):
        allocation = self.allocations.get(client)
        if allocation is not None and allocation.username != username:
            raise TurnError(441)
        if kind == ALLOCATE:
            if allocation is not None:
                raise TurnError(437)
            if self.one(attrs, TRANSPORT) != b'\x11\x00\x00\x00':
                raise TurnError(442)
            family = self.one(attrs, FAMILY, b'\x01\x00\x00\x00')
            if family not in (b'\x01\x00\x00\x00', b'\x02\x00\x00\x00'):
                raise TurnError(400)
            if (4 if family[0] == 1 else 6) != self.family:
                raise TurnError(440)
            if len(self.allocations) >= self.max_allocations:
                raise TurnError(486)
            lifetime = self.lifetime(attrs)
            allocation = await self.allocate(client, username)
            relay = (self.external_address, allocation.transport.get_extra_info('sockname')[1])
            allocation.refresh(lifetime)
            return [(RELAYED, xor_address(relay, transaction)), (MAPPED, xor_address(client, transaction)),
                    (LIFETIME, struct.pack('!I', lifetime))]
        if allocation is None:
            raise TurnError(437)
        if kind == REFRESH:
            lifetime = self.lifetime(attrs)
            allocation.refresh(lifetime)
            return [(LIFETIME, struct.pack('!I', lifetime))]
        if kind == PERMISSION:
            peers = [self.peer(value, transaction) for value in attrs.get(PEER, [])]
            if not peers:
                raise TurnError(400)
            now = time.monotonic()
            allocation.permissions = {h: expiry for h, expiry in allocation.permissions.items() if expiry > now}
            if len(set(allocation.permissions) | {h for h, _ in peers}) > 256:
                raise TurnError(508)
            for host, _ in peers:
                allocation.permissions[host] = now + 300
            return []
        if kind == CHANNEL_BIND:
            value = self.one(attrs, CHANNEL)
            if len(value) != 4 or value[2:] != b'\x00\x00':
                raise TurnError(400)
            channel = int.from_bytes(value[:2], 'big')
            if not 0x4000 <= channel <= 0x7FFF:
                raise TurnError(400)
            peer = self.peer(self.one(attrs, PEER), transaction)
            now = time.monotonic()
            allocation.channels = {c: b for c, b in allocation.channels.items() if b[1] > now}
            allocation.permissions = {h: expiry for h, expiry in allocation.permissions.items() if expiry > now}
            if (channel not in allocation.channels and len(allocation.channels) >= 256) or (
                    peer[0] not in allocation.permissions and len(allocation.permissions) >= 256):
                raise TurnError(508)
            if any((c == channel) != (bound[0] == peer) for c, bound in allocation.channels.items()):
                raise TurnError(400)
            allocation.channels[channel] = (peer, now + 600)
            allocation.permissions[peer[0]] = now + 300
            return []
        raise TurnError(400)

    async def process_requests(self):
        while True:
            wire, client, parsed = await self.queue.get()
            kind, transaction, attrs = parsed
            cache_key = (client, transaction, hashlib.sha256(wire).digest())
            cached = self.responses.get(cache_key)
            if cached is not None and cached[0] > time.monotonic():
                self.transport.sendto(cached[1], client)
                continue
            key = None
            try:
                if kind == BINDING:
                    response_attrs = [(MAPPED, xor_address(client, transaction))]
                else:
                    username, key, values = self.authenticate(wire, client, attrs)
                    response_attrs = await self.process(kind, transaction, client, username, values)
                response = message(kind | 0x0100, transaction, response_attrs, key)
            except (TurnError, ValueError) as error:
                code = error.code if isinstance(error, TurnError) else 400
                reason = REASONS[code].encode()
                response_attrs = [(ERROR, bytes([0, 0, code // 100, code % 100]) + reason)]
                if code in (401, 438):
                    response_attrs += [(REALM, self.realm), (NONCE, self.nonce(client))]
                if isinstance(error, TurnError):
                    response_attrs += list(error.attrs)
                    key = key or error.key
                response = message(kind | 0x0110, transaction, response_attrs, key)
            if len(self.responses) >= 1024:
                self.responses.pop(next(iter(self.responses)))
            self.responses[cache_key] = (time.monotonic() + 40, response)
            self.transport.sendto(response, client)

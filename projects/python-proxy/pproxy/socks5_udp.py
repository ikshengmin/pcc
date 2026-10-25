"""SOCKS5 UDP associations, owned by their TCP control connection."""
import asyncio
import ipaddress
import socket
import time

from . import proto

QUEUE_LIMIT = 256
DNS_CACHE_LIMIT = 256
DNS_CACHE_SECONDS = 60


def ip_key(host):
    ip = ipaddress.ip_address(host)
    return getattr(ip, 'ipv4_mapped', None) or ip


class DatagramEndpoint(asyncio.DatagramProtocol):
    def __init__(self, receive):
        self.receive = receive
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.receive(data, addr)

    def error_received(self, error):
        # UDP errors belong to a datagram, not the lifetime of the association.
        pass


class DirectRelay:
    def __init__(self, receive, lbind=None, timeout=60):
        self.receive = receive
        self.lbind = lbind
        self.timeout = timeout
        self.transports = {}
        self.cache = {}

    async def send(self, host, port, data):
        loop = asyncio.get_event_loop()
        key = (host, port)
        cached = self.cache.get(key)
        if cached is None or cached[0] < time.monotonic():
            family = 0 if not self.lbind else socket.AF_INET6 if ':' in self.lbind else socket.AF_INET
            results = await asyncio.wait_for(
                loop.getaddrinfo(host, port, family=family, type=socket.SOCK_DGRAM), self.timeout)
            family, _, _, _, addr = results[0]
            if len(self.cache) >= DNS_CACHE_LIMIT:
                self.cache.pop(next(iter(self.cache)))
            cached = (time.monotonic() + DNS_CACHE_SECONDS, family, addr)
            self.cache[key] = cached
        _, family, addr = cached
        if family not in self.transports:
            bind = self.lbind or ('::' if family == socket.AF_INET6 else '0.0.0.0')
            transport, _ = await loop.create_datagram_endpoint(
                lambda: DatagramEndpoint(self.receive), local_addr=(bind, 0), family=family)
            self.transports[family] = transport
        # One unconnected socket per family preserves the NAT/STUN mapping
        # while ICE probes multiple destinations and receives peer candidates.
        self.transports[family].sendto(data, addr)

    async def close(self):
        for transport in self.transports.values():
            transport.close()
        self.transports.clear()
        self.cache.clear()


class Socks5Relay:
    def __init__(self, remote, receive, disconnected, lbind=None, timeout=60):
        self.remote = remote
        self.receive = receive
        self.disconnected = disconnected
        self.lbind = lbind
        self.timeout = timeout
        self.writer = None
        self.transport = None
        self.monitor = None
        self.closed = False
        self.counted = False
        self.updated = time.monotonic()

    async def start(self):
        remote = self.remote
        if (not isinstance(remote.rproto, proto.Socks5) or remote.cipher
                or not remote.jump.direct or remote.unix):
            raise ValueError('SOCKS5 UDP requires a standalone SOCKS5 upstream (no __ chain or cipher)')
        try:
            reader, self.writer = await remote.open_connection(None, 0, None, self.lbind, self.timeout)
            reader, self.writer = proto.sslwrap(
                reader, self.writer, remote.sslclient, False, remote.host_name)
            host, port = await asyncio.wait_for(
                remote.rproto.request(reader, self.writer, remote.auth, '0.0.0.0', 0, 3), self.timeout)
            if not port:
                raise ValueError('SOCKS5 upstream returned no UDP relay port')
            if host in ('0.0.0.0', '::'):
                host = self.writer.get_extra_info('peername')[0]
            # BND.ADDR may name a relay on a different address family than
            # the TCP proxy. Only constrain the UDP bind when requested.
            local_addr = (self.lbind, 0) if self.lbind else None
            self.transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: DatagramEndpoint(self.received), remote_addr=(host, port),
                local_addr=local_addr)
            self.remote.connection_change(1)
            self.counted = True
            self.monitor = asyncio.ensure_future(self.watch_control(reader))
        except BaseException:
            await self.close()
            raise

    async def watch_control(self, reader):
        try:
            await reader.read(1)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            if not self.closed:
                self.close_sockets()
                self.disconnected()

    def received(self, data, addr):
        packet = proto.socks5_udp_unpack(data)
        if packet is not None and not self.closed:
            self.updated = time.monotonic()
            host, port, payload = packet
            self.receive(payload, (host, port))

    async def send(self, host, port, data):
        if self.closed:
            raise ConnectionError('SOCKS5 UDP control connection closed')
        self.transport.sendto(b'\x00\x00\x00' + proto.socks5_address(host, port) + data)
        self.updated = time.monotonic()

    def close_sockets(self):
        self.closed = True
        if self.counted:
            self.remote.connection_change(-1)
            self.counted = False
        if self.transport:
            self.transport.close()
        if self.writer:
            self.writer.close()

    async def close(self):
        self.close_sockets()
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.writer and hasattr(self.writer, 'wait_closed'):
            try:
                await self.writer.wait_closed()
            except OSError:
                pass


class UDPAssociation:
    def __init__(self, reader, writer, request, remotes, schedule, block, verbose, lbind, timeout):
        self.reader = reader
        self.writer = writer
        self.request = request
        self.remotes = remotes
        self.schedule = schedule
        self.block = block
        self.verbose = verbose
        self.lbind = lbind
        self.timeout = timeout
        self.peer = ip_key(writer.get_extra_info('peername')[0])
        self.client = None
        self.transport = None
        self.relays = {}
        self.routes = {}
        self.queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self.closed = False

    def received(self, data, addr):
        if self.closed or ip_key(addr[0]) != self.peer:
            return
        if self.request.port and addr[1] != self.request.port:
            return
        if self.client is not None and addr != self.client:
            return
        packet = proto.socks5_udp_unpack(data)
        if packet is None or not packet[1]:
            return
        if self.client is None:
            self.client = addr
        try:
            self.queue.put_nowait(packet)
        except asyncio.QueueFull:
            pass

    def reply(self, data, addr):
        if self.closed or self.client is None:
            return
        try:
            wire = b'\x00\x00\x00' + proto.socks5_address(*addr[:2]) + data
            self.transport.sendto(wire, self.client)
        except (OSError, ValueError):
            pass

    async def forward(self):
        while True:
            host, port, data = await self.queue.get()
            try:
                if self.block and self.block(host):
                    continue
                key = (host, port)
                if key not in self.routes:
                    remote = self.schedule(self.remotes, host, port)
                    if remote is None and any(r.match_rule(host, port) for r in self.remotes):
                        raise ConnectionError('No available UDP upstream')
                    if len(self.routes) >= DNS_CACHE_LIMIT:
                        self.routes.pop(next(iter(self.routes)))
                    self.routes[key] = remote
                remote = self.routes[key]
                if remote not in self.relays:
                    bind = getattr(remote, 'lbind', None) or self.lbind
                    if bind == 'in':
                        bind = self.writer.get_extra_info('sockname')[0]
                    if remote is None or remote.direct:
                        relay = DirectRelay(self.reply, bind, self.timeout)
                    else:
                        relay = Socks5Relay(remote, self.reply, self.writer.close, bind, self.timeout)
                        await relay.start()
                    self.relays[remote] = relay
                await self.relays[remote].send(host, port, data)
            except Exception as error:
                self.verbose(f'SOCKS5 UDP {host}:{port}: {error}')

    async def run(self):
        worker = None
        try:
            if self.request.host not in ('0.0.0.0', '::'):
                try:
                    valid = ip_key(self.request.host) == self.peer
                except ValueError:
                    addresses = await asyncio.get_event_loop().getaddrinfo(
                        self.request.host, self.request.port, type=socket.SOCK_DGRAM)
                    valid = any(ip_key(a[4][0]) == self.peer for a in addresses)
                if not valid:
                    raise proto.Socks5Error(2)
            for remote in self.remotes:
                if not remote.direct and (not isinstance(getattr(remote, 'rproto', None), proto.Socks5)
                        or remote.cipher or not remote.jump.direct or remote.unix):
                    raise proto.Socks5Error(7)
            local = self.writer.get_extra_info('sockname')
            self.transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: DatagramEndpoint(self.received), local_addr=(local[0], 0))
            self.request.address = proto.socks5_address(*self.transport.get_extra_info('sockname')[:2])
            await self.request(None)
            await self.writer.drain()
            worker = asyncio.ensure_future(self.forward())
            # No handshake timeout on the live control connection. Its EOF,
            # cancellation or unexpected data terminates the UDP association.
            await self.reader.read(1)
        finally:
            self.closed = True
            if self.transport:
                self.transport.close()
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            await asyncio.gather(*(r.close() for r in self.relays.values()))
            # On setup failure, stream_handler still needs to send REP.
            if self.request.sent:
                self.writer.close()

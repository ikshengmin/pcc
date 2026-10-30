"""HTTP/3 CONNECT and CONNECT-UDP for Chrome's native QUIC proxy client.

Uses pproxy's existing optional aioquic dependency. TURN packets can share the
UDP listener and travel through the existing SOCKS5 upstream to a TURN server.
"""
import asyncio
import base64
import hmac
import time
import urllib.parse

from aioquic.asyncio import QuicConnectionProtocol
from aioquic.asyncio.server import QuicServer
from aioquic.buffer import Buffer, BufferReadError
from aioquic.h3.connection import H3Connection, Setting
from aioquic.h3.events import DataReceived, DatagramReceived, HeadersReceived
from aioquic.quic.events import ConnectionTerminated, StreamReset

from . import proto
from .socks5_udp import DirectRelay, Socks5Relay

STREAM_BUFFER = 1024 * 1024
MAX_STREAMS = 256
DATAGRAM_QUEUE = 128
H3_CANCELLED = 0x10C


class DatagramH3(H3Connection):
    def _get_local_settings(self):
        settings = super()._get_local_settings()
        settings[Setting.H3_DATAGRAM] = 1
        settings[Setting.ENABLE_CONNECT_PROTOCOL] = 1
        return settings


def datagram_payload(data):
    try:
        buffer = Buffer(data=data)
        if buffer.pull_uint_var() != 0:
            return None
        return data[buffer.tell():]
    except BufferReadError:
        return None


class Flow:
    def __init__(self, protocol, stream_id, host, port, udp):
        self.protocol, self.stream_id = protocol, stream_id
        self.host, self.port, self.udp = host, port, udp
        self.queue = asyncio.Queue(maxsize=DATAGRAM_QUEUE)
        self.pending_bytes = 0
        self.capsules = bytearray()
        self.writer = self.relay = None
        self.tasks = []
        self.closed = self.accepted = False

    def enqueue(self, data):
        if self.closed:
            return
        size = len(data) if data is not None else 0
        if self.pending_bytes + size > STREAM_BUFFER or self.queue.full():
            if not self.udp:
                self.close(reset=True)
            return
        self.pending_bytes += size
        self.queue.put_nowait(data)

    def consume_capsules(self, data):
        self.capsules.extend(data)
        if len(self.capsules) > STREAM_BUFFER:
            self.close(reset=True)
            return
        while self.capsules:
            buffer = Buffer(data=bytes(self.capsules))
            try:
                kind, size = buffer.pull_uint_var(), buffer.pull_uint_var()
            except BufferReadError:
                return
            if size > 65536:
                self.close(reset=True)
                return
            end = buffer.tell() + size
            if end > len(self.capsules):
                return
            payload = bytes(self.capsules[buffer.tell():end])
            del self.capsules[:end]
            if kind == 0:
                payload = datagram_payload(payload)
                if payload is not None:
                    self.enqueue(payload)

    def headers(self, status, end=False):
        headers = [(b':status', str(status).encode())]
        if self.udp and status == 200:
            headers.append((b'capsule-protocol', b'?1'))
        self.protocol.http.send_headers(self.stream_id, headers, end_stream=end)
        self.protocol.transmit()
        self.accepted = status == 200

    def close(self, reset=False):
        if self.closed:
            return
        self.closed = True
        if self.writer:
            self.writer.close()
        current = asyncio.current_task(loop=asyncio.get_event_loop())
        for task in self.tasks:
            if task is not current:
                task.cancel()
        if reset:
            self.protocol._quic.reset_stream(self.stream_id, H3_CANCELLED)
            self.protocol.transmit()
        self.protocol.flows.pop(self.stream_id, None)

    def reply_udp(self, data, address):
        if not self.closed:
            self.protocol.http.send_datagram(self.stream_id, b'\x00' + data)
            self.protocol.transmit()

    async def run(self):
        from .server import DIRECT, SOCKET_TIMEOUT, schedule
        remote = None
        counted = False
        try:
            args = self.protocol.options
            if args.get('block') and args['block'](self.host):
                self.headers(403, True)
                return
            choices = args.get('urserver' if self.udp else 'rserver', [])
            remote = schedule(choices, args.get('salgorithm', 'fa'), self.host, self.port)
            if remote is None and any(r.match_rule(self.host, self.port) for r in choices):
                self.headers(502, True)
                return
            remote = remote or DIRECT
            if self.udp:
                if remote.direct:
                    self.relay = DirectRelay(self.reply_udp, remote.lbind)
                else:
                    self.relay = Socks5Relay(remote, self.reply_udp,
                        lambda: self.close(reset=True), timeout=SOCKET_TIMEOUT)
                    await self.relay.start()
                self.headers(200)
                while not self.closed:
                    data = await self.queue.get()
                    if data is None:
                        break
                    self.pending_bytes -= len(data)
                    await self.relay.send(self.host, self.port, data)
            else:
                reader, self.writer = await remote.tcp_connect(self.host, self.port)
                remote.connection_change(1)
                counted = True
                self.headers(200)
                up = asyncio.ensure_future(self.write_upstream())
                down = asyncio.ensure_future(self.read_upstream(reader))
                self.tasks.extend([up, down])
                await asyncio.gather(up, down)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.protocol.verbose(f'MASQUE {self.host}:{self.port}: {error}')
            if not self.closed:
                if not self.accepted:
                    self.headers(502, True)
                else:
                    self.close(reset=True)
        finally:
            if self.relay:
                await self.relay.close()
            if counted:
                remote.connection_change(-1)
            self.close()

    async def write_upstream(self):
        while not self.closed:
            data = await self.queue.get()
            if data is None:
                if getattr(self.writer, 'can_write_eof', lambda: False)():
                    self.writer.write_eof()
                return
            self.pending_bytes -= len(data)
            self.writer.write(data)
            await self.writer.drain()

    async def read_upstream(self, reader):
        while not self.closed:
            # aioquic retains unacknowledged stream bytes. Bound the producer
            # when an HTTP/3 client reads slowly instead of buffering the origin.
            stream = self.protocol._quic._streams.get(self.stream_id)
            if stream is not None and len(stream.sender._buffer) > STREAM_BUFFER:
                await asyncio.sleep(0.005)
                continue
            data = await reader.read(65536)
            self.protocol.http.send_data(self.stream_id, data, end_stream=not data)
            self.protocol.transmit()
            if not data:
                return
            await asyncio.sleep(0)


class MasqueProtocol(QuicConnectionProtocol):
    def __init__(self, *args, owner, options, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner, self.options = owner, options
        self.verbose = options.get('verbose', lambda message: None)
        self.http = DatagramH3(self._quic)
        self.flows = {}

    def close(self, *args, **kwargs):
        for flow in list(self.flows.values()):
            flow.close()
        super().close(*args, **kwargs)

    def quic_event_received(self, event):
        if isinstance(event, ConnectionTerminated):
            for flow in list(self.flows.values()):
                flow.close()
        if isinstance(event, StreamReset) and event.stream_id in self.flows:
            self.flows[event.stream_id].close()
        for item in self.http.handle_event(event):
            if isinstance(item, HeadersReceived):
                self.request(item)
            elif isinstance(item, DatagramReceived):
                flow = self.flows.get(item.stream_id)
                if flow and flow.udp:
                    data = datagram_payload(item.data)
                    if data is not None:
                        flow.enqueue(data)
            elif isinstance(item, DataReceived):
                flow = self.flows.get(item.stream_id)
                if flow:
                    if flow.udp:
                        flow.consume_capsules(item.data)
                    elif item.data:
                        flow.enqueue(item.data)
                    if item.stream_ended:
                        flow.enqueue(None)

    def reject(self, stream_id, status):
        headers = [(b':status', str(status).encode())]
        if status == 407:
            headers.append((b'proxy-authenticate', b'Basic realm="pproxy"'))
        self.http.send_headers(stream_id, headers, end_stream=True)
        self.transmit()

    def request(self, event):
        if event.stream_id in self.flows:
            return
        headers = dict(event.headers)
        if self.owner.users:
            authorization = headers.get(b'proxy-authorization', b'')
            if not any(hmac.compare_digest(authorization, b'Basic ' + base64.b64encode(user)) for user in self.owner.users):
                self.reject(event.stream_id, 407)
                return
        if headers.get(b':method') != b'CONNECT':
            self.reject(event.stream_id, 405)
            return
        udp = headers.get(b':protocol') == b'connect-udp'
        try:
            if headers.get(b':protocol') and not udp:
                raise ValueError('Unsupported Extended CONNECT protocol')
            if udp:
                path = headers[b':path'].decode('ascii').split('/')
                if len(path) != 7 or path[:4] != ['', '.well-known', 'masque', 'udp'] or path[-1]:
                    raise ValueError('Invalid CONNECT-UDP URI')
                host, port = urllib.parse.unquote(path[4]), int(path[5])
            else:
                host, port = proto.netloc_split(headers[b':authority'].decode('ascii'))
            if not host or any(c.isspace() or c in '\x00/\\' for c in host) or not port or not 0 < port < 65536:
                raise ValueError('Invalid target')
        except (KeyError, ValueError, UnicodeError):
            self.reject(event.stream_id, 400)
            return
        if len(self.flows) >= MAX_STREAMS:
            self.reject(event.stream_id, 503)
            return
        flow = Flow(self, event.stream_id, host, port, udp)
        self.flows[event.stream_id] = flow
        task = asyncio.ensure_future(flow.run())
        flow.tasks.append(task)
        if event.stream_ended:
            flow.enqueue(None)


async def serve(owner, options):
    from .server import datagram_handler
    target = owner.rproto.param
    class Server(QuicServer):
        def __init__(self):
            super().__init__(configuration=owner.quicserver,
                create_protocol=lambda *a, **kw: MasqueProtocol(*a, owner=owner, options=options, **kw))
            self.turn_sources = {}
            self.queue = asyncio.Queue(maxsize=256)
            self.worker = asyncio.ensure_future(self.forward_turn())

        @property
        def sockets(self):
            return [self._transport.get_extra_info('socket')]

        def close(self):
            self.worker.cancel()
            super().close()

        def datagram_received(self, data, addr):
            stun = len(data) >= 20 and data[0] & 0xC0 == 0 and data[4:8] == b'\x21\x12\xa4\x42'
            if data and data[0] & 0xC0 == 0xC0:
                self.turn_sources.pop(addr, None)
            turn = target and (stun or (addr in self.turn_sources and time.monotonic() - self.turn_sources[addr] < options.get('udp_idle', 900)))
            if turn:
                if len(self.turn_sources) >= 1024:
                    self.turn_sources.pop(min(self.turn_sources, key=self.turn_sources.get))
                self.turn_sources[addr] = time.monotonic()
                try:
                    self.queue.put_nowait((data, addr))
                except asyncio.QueueFull:
                    pass
            else:
                super().datagram_received(data, addr)

        async def forward_turn(self):
            while True:
                data, addr = await self.queue.get()
                await datagram_handler(self._transport, data, addr, [proto.Tunnel(target)],
                    options.get('urserver', []), options.get('block'), None,
                    options.get('salgorithm', 'fa'), options.get('verbose', lambda message: None))
    _, instance = await asyncio.get_event_loop().create_datagram_endpoint(Server, local_addr=(owner.host_name, owner.port))
    return instance

"""Run pproxy with metadata-only connection diagnostics; payloads are never logged."""
import asyncio
import contextvars
import hashlib
import itertools
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import sys
import time
import weakref

from . import proto, server
from .diagnostic_log import LogPolicy, expected_disconnect, failure_metadata


def main():
    POLICY = LogPolicy(os.environ.get('PPROXY_TRACE_MODE', 'essential'))

    path = Path(os.environ['PPROXY_TRACE_PATH'])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.close(fd)
    logger = logging.getLogger('pproxy.connection-trace')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    sink = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8')
    sink.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(sink)
    FLOW = contextvars.ContextVar('trace_flow', default=0)
    CLIENT_WRITER = contextvars.ContextVar('trace_client_writer', default=0)
    TARGET = contextvars.ContextVar('trace_target', default=None)
    CONNECTION = contextvars.ContextVar('trace_connection', default=None)
    IDS = itertools.count(1)
    READERS = weakref.WeakKeyDictionary()
    WRITERS = weakref.WeakKeyDictionary()
    WATCHED_LOOPS = weakref.WeakSet()
    UPSTREAM_AVAILABLE = {}


    def emit(event, **fields):
        if event.endswith('_error'):
            fields.setdefault('phase', event[:-6])
        selected = POLICY.select(event, fields, CONNECTION.get() or {})
        if selected is None:
            return
        event, fields = selected
        logger.info(json.dumps({'time': time.time(), 'mono': time.monotonic(),
                               'pid': os.getpid(), 'flow': FLOW.get(),
                               'event': event, **fields}, default=str))


    def emit_error(event, error, **fields):
        if POLICY.mode != 'diagnostic':
            closing = event in ('client_handshake_error', 'channel_error',
                                'read_error', 'write_error', 'drain_error')
            if (isinstance(error, (asyncio.CancelledError, KeyboardInterrupt))
                    or closing and expected_disconnect(error)
                    or (CONNECTION.get() or {}).get('issue_logged')):
                return
        emit(event, **fields, **failure_metadata(error))


    def channel_error(self, error, phase):
        emit_error('channel_error', error, phase=phase, protocol=self.name)


    def health_status(bind, available, error=None):
        previous = UPSTREAM_AVAILABLE.get(bind, True)
        UPSTREAM_AVAILABLE[bind] = available
        if previous != available:
            if error is None:
                emit('upstream_status', bind=bind, available=available)
            else:
                emit_error('upstream_status', error, bind=bind, available=available)


    def safe_host(host):
        return str(host).split('?', 1)[0].split('#', 1)[0].rsplit('@', 1)[-1][:255]


    def snapshot(state):
        now = time.monotonic()
        reader, writer = state['reader'](), state['writer']()
        return {'flow': state['flow'], 'direction': state['direction'], 'target': state['target'],
                'age_ms': round((now - state['start']) * 1000),
                'read_bytes': state['read_bytes'], 'written_bytes': state['written_bytes'],
                'read_idle_ms': round((now - state['last_read']) * 1000),
                'write_idle_ms': round((now - state['last_write']) * 1000),
                'reader_eof': reader.at_eof() if reader else None,
                'unread_bytes': len(reader._buffer) if reader else None,
                'pending_write_bytes': writer.transport.get_write_buffer_size() if writer else None,
                'reason': state['reason']}


    original_handler = server.stream_handler
    original_start = server.ProxySimple.start_server
    original_accept = proto.accept
    original_schedule = server.schedule
    original_open = server.ProxyDirect.open_connection
    original_prepare = server.ProxySimple.prepare_connection
    original_channel = proto.BaseProtocol.channel
    original_read = asyncio.StreamReader.read
    original_write = asyncio.StreamWriter.write
    original_drain = asyncio.StreamWriter.drain
    original_close = asyncio.StreamWriter.close
    original_loop = server.get_or_create_event_loop


    async def handler(reader, writer, *args, **kwargs):
        token = FLOW.set(next(IDS))
        client = CLIENT_WRITER.set(id(writer))
        target = TARGET.set(None)
        context = CONNECTION.set({'client': writer.get_extra_info('peername'),
                                  'local': writer.get_extra_info('sockname'), 'client_bytes': 0})
        if POLICY.mode == 'diagnostic':
            emit('accepted', client=writer.get_extra_info('peername'), local=writer.get_extra_info('sockname'))
        try:
            return await original_handler(reader, writer, *args, **kwargs)
        except BaseException as error:
            emit_error('handler_error', error)
            raise
        finally:
            TARGET.reset(target)
            CLIENT_WRITER.reset(client)
            FLOW.reset(token)
            CONNECTION.reset(context)


    def start(self, args, stream_handler=handler):
        return original_start(self, args, stream_handler=handler)


    async def accept(*args, **kwargs):
        try:
            result = await original_accept(*args, **kwargs)
            target = (safe_host(result[2]), result[3])
            TARGET.set(target)
            context = CONNECTION.get()
            if context is not None:
                context.update(target=target, protocol=result[0].name)
            if POLICY.mode == 'diagnostic':
                emit('client_protocol', protocol=result[0].name, target=target)
            return result
        except BaseException as error:
            emit_error('client_handshake_error', error)
            raise


    def schedule(candidates, algorithm, host, port):
        result = original_schedule(candidates, algorithm, host, port)
        context = CONNECTION.get()
        if context is not None:
            context['upstream'] = result.bind if result else 'DIRECT'
        if result is None and any(p.match_rule(host, port) for p in candidates):
            emit('direct_fallback', target=(safe_host(host), port), selected='DIRECT')
        if POLICY.mode == 'diagnostic':
            emit('route_selected', target=(safe_host(host), port), algorithm=algorithm,
                 selected=result.bind if result else 'DIRECT_FALLBACK',
                 candidates=[{'bind': p.bind, 'alive': p.alive} for p in candidates])
        return result


    async def open_connection(self, *args, **kwargs):
        started = time.monotonic() if POLICY.mode == 'diagnostic' else None
        try:
            reader, writer = await original_open(self, *args, **kwargs)
            if FLOW.get() == 0:
                health_status(self.bind, True)
            if POLICY.mode == 'diagnostic':
                emit('upstream_connected', bind=self.bind, peer=writer.get_extra_info('peername'),
                     local=writer.get_extra_info('sockname'), ms=round((time.monotonic() - started) * 1000),
                     healthcheck=FLOW.get() == 0)
            return reader, writer
        except BaseException as error:
            if FLOW.get() == 0:
                if not isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
                    health_status(self.bind, False, error)
                if POLICY.mode == 'diagnostic':
                    emit_error('upstream_connect_error', error, bind=self.bind)
            else:
                emit_error('upstream_connect_error', error, bind=self.bind)
            raise


    async def prepare(self, reader, writer, host, port):
        started = time.monotonic() if POLICY.mode == 'diagnostic' else None
        try:
            result = await original_prepare(self, reader, writer, host, port)
            if POLICY.mode == 'diagnostic':
                emit('upstream_handshake_complete', bind=self.bind,
                     ms=round((time.monotonic() - started) * 1000))
            return result
        except BaseException as error:
            emit_error('upstream_handshake_error', error, bind=self.bind)
            raise


    async def read(reader, n=-1):
        state = READERS.get(reader)
        try:
            data = await original_read(reader, n)
        except BaseException as error:
            if state:
                state['reason'] = 'read_' + type(error).__name__
                emit_error('read_error', error, **snapshot(state))
            raise
        if state:
            if data:
                first = state['read_bytes'] == 0
                state['read_bytes'] += len(data)
                context = CONNECTION.get()
                if context is not None and state['direction'] == 'client_to_upstream':
                    context['client_bytes'] += len(data)
                state['last_read'] = time.monotonic()
                if first: emit('first_data', **snapshot(state))
            else:
                state['reason'] = 'eof'
                emit('eof', **snapshot(state))
        return data


    def write(writer, data):
        state = WRITERS.get(writer)
        try:
            result = original_write(writer, data)
            if state:
                state['written_bytes'] += len(data)
                state['last_write'] = time.monotonic()
            return result
        except BaseException as error:
            if state:
                state['reason'] = 'write_' + type(error).__name__
                emit_error('write_error', error, **snapshot(state))
            raise


    async def drain(writer):
        try:
            return await original_drain(writer)
        except BaseException as error:
            state = WRITERS.get(writer)
            if state:
                state['reason'] = 'drain_' + type(error).__name__
                emit_error('drain_error', error, **snapshot(state))
            raise


    def close(writer):
        state = WRITERS.get(writer)
        if state and not state['close_logged']:
            state['close_logged'] = True
            emit('writer_close', caller=sys._getframe(1).f_code.co_name,
                 peer=writer.get_extra_info('peername'), **snapshot(state))
        return original_close(writer)


    async def channel(self, reader, writer, stat_bytes, stat_conn):
        now = time.monotonic()
        state = {'flow': FLOW.get(), 'target': TARGET.get(),
                 'direction': 'upstream_to_client' if id(writer) == CLIENT_WRITER.get() else 'client_to_upstream',
                 'reader': weakref.ref(reader), 'writer': weakref.ref(writer),
                 'start': now, 'last_read': now, 'last_write': now,
                 'read_bytes': 0, 'written_bytes': 0, 'reason': 'active', 'close_logged': False}
        READERS[reader] = state
        WRITERS[writer] = state
        emit('channel_start', **snapshot(state))
        try:
            return await original_channel(self, reader, writer, stat_bytes, stat_conn)
        finally:
            emit('channel_end', **snapshot(state))
            READERS.pop(reader, None)
            WRITERS.pop(writer, None)


    def event_loop():
        loop = original_loop()
        if POLICY.mode != 'diagnostic':
            return loop
        if loop not in WATCHED_LOOPS:
            WATCHED_LOOPS.add(loop)
            reference = weakref.ref(loop)
            def report():
                current = reference()
                if current is None or current.is_closed(): return
                for state in list(WRITERS.values()): emit('active_connection', **snapshot(state))
                current.call_later(15, report)
            loop.call_later(15, report)
        return loop


    def shutdown(signum, frame):
        emit('shutdown_requested', signal=signum)
        raise KeyboardInterrupt


    server.ProxySimple.start_server = start
    proto.accept = accept
    server.schedule = schedule
    server.ProxyDirect.open_connection = open_connection
    server.ProxySimple.prepare_connection = prepare
    if POLICY.mode == 'diagnostic':
        proto.BaseProtocol.channel = channel
        asyncio.StreamReader.read = read
        asyncio.StreamWriter.write = write
        asyncio.StreamWriter.drain = drain
        asyncio.StreamWriter.close = close
        server.get_or_create_event_loop = event_loop
    else:
        proto.BaseProtocol.channel_error = channel_error
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    emit('process_start', python=sys.version.split()[0], log_mode=POLICY.mode,
         source={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (Path(server.__file__), Path(proto.__file__),
                           Path(sys.modules[LogPolicy.__module__].__file__), Path(__file__))})
    try:
        server.main()
    finally:
        emit('process_stop')


if __name__ == '__main__':
    main()

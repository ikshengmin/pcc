import asyncio
import errno
import inspect
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import sys

import pytest

from pproxy import proto, server
from pproxy.diagnostic_log import LogPolicy, expected_disconnect, failure_metadata

from test_proxy_stability import network, tunnel


@pytest.mark.parametrize('event', ['accepted', 'client_protocol', 'route_selected', 'upstream_connected',
                                  'upstream_handshake_complete', 'first_data', 'active_connection',
                                  'channel_start', 'channel_end', 'writer_close'])
def test_essential_omits_normal_connection_events(event):
    assert LogPolicy().select(event, {}, {}) is None


@pytest.mark.parametrize('event', ['process_start', 'process_stop', 'shutdown_requested', 'upstream_status'])
def test_lifecycle_and_upstream_transitions_are_kept(event):
    assert LogPolicy().select(event, {'available': False}, {}) == (event, {'available': False})


def test_failure_keeps_context_but_logs_only_once_per_connection():
    state = {'client': ('127.0.0.1', 1234), 'target': ('example.test', 443), 'upstream': 'remote:8084'}
    policy = LogPolicy()
    event, data = policy.select('upstream_connect_error', {'error_type': 'TimeoutError'}, state)
    assert event == 'upstream_connect_error' and data['target'] == ('example.test', 443)
    assert data['upstream'] == 'remote:8084'
    assert policy.select('handler_error', {'error_type': 'TimeoutError'}, state) is None


@pytest.mark.parametrize('error', ['CancelledError', 'KeyboardInterrupt'])
def test_expected_cancellation_is_silent(error):
    assert LogPolicy().select('read_error', {'error_type': error}, {}) is None


def test_client_handshake_abandonment_is_silent_but_upstream_truncation_is_not():
    fields = {'error_type': 'IncompleteReadError', 'expected_disconnect': True}
    assert LogPolicy().select('client_handshake_error', fields, {}) is None
    assert LogPolicy().select('upstream_handshake_error', fields, {}) is not None


def test_zero_response_upstream_close_is_actionable():
    state = {'client_bytes': 1900, 'target': ('video.example', 443)}
    result = LogPolicy().select('eof', {'direction': 'upstream_to_client', 'read_bytes': 0}, state)
    assert result[0] == 'upstream_closed_without_response'


@pytest.mark.parametrize('received,sent', [(0, 0), (100, 1900)])
def test_unused_or_successful_upstream_close_is_silent(received, sent):
    assert LogPolicy().select('eof', {'direction': 'upstream_to_client', 'read_bytes': received}, {'client_bytes': sent}) is None


def test_client_close_does_not_turn_peer_cleanup_into_an_upstream_failure():
    policy, state = LogPolicy(), {'client_bytes': 1900}
    assert policy.select('eof', {'direction': 'client_to_upstream', 'read_bytes': 1900}, state) is None
    assert policy.select('eof', {'direction': 'upstream_to_client', 'read_bytes': 0}, state) is None


def test_diagnostic_mode_keeps_full_events():
    policy = LogPolicy('diagnostic')
    assert policy.select('active_connection', {'read_bytes': 12}, {}) == ('active_connection', {'read_bytes': 12})


def test_unexpected_direct_fallback_is_kept():
    assert LogPolicy().select('direct_fallback', {'selected': 'DIRECT'}, {}) is not None


@pytest.mark.parametrize('error', [ConnectionResetError(54, 'reset'), BrokenPipeError(),
                                  asyncio.CancelledError(), EOFError(),
                                  OSError(errno.ENOTCONN, 'Socket is not connected')])
def test_routine_disconnect_can_be_filtered_before_collecting_frames(error):
    assert expected_disconnect(error)


@pytest.mark.parametrize('error', [AssertionError(), TimeoutError(), ConnectionRefusedError(),
                                  OSError(errno.EHOSTUNREACH, 'No route to host'), OSError()])
def test_real_failures_are_not_classified_as_routine_disconnects(error):
    assert not expected_disconnect(error)


def test_socks5_reply_code_is_recorded_without_the_message():
    fields = failure_metadata(proto.Socks5Error(5))
    assert fields['error_type'] == 'Socks5Error' and fields['reply'] == 5
    assert 'SOCKS5 upstream failure' not in json.dumps(fields)

    class PrivateReply(Exception):
        reply = 'private-reply-marker'

    assert 'reply' not in failure_metadata(PrivateReply())


def test_client_handshake_deadline_keeps_its_timeout_type(monkeypatch):
    """A client that connects and sends nothing hits the read deadline in the
    first guess(); the record must name that deadline, not a generic closure."""
    monkeypatch.setattr(server, 'SOCKET_TIMEOUT', 0.05)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError) as caught:
            await proto.accept([proto.HTTP(None), proto.Socks5(None)], reader=asyncio.StreamReader())
        return caught.value

    fields = failure_metadata(asyncio.run(asyncio.wait_for(scenario(), 3)))
    assert fields['error_type'] == 'TimeoutError'
    assert any(frame['function'] == 'HTTP.guess' for frame in fields['traceback'])
    assert fields['expected_disconnect'] is False


async def echo_until_released(reader, writer, client_eof, release):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        client_eof.set()
        await release.wait()
    finally:
        writer.close()


async def reset_after_half_close(net, proxy_port, target, client_eof, release):
    """Half-close, then reset before the upstream closes, so the proxy's later
    write_eof() toward the client meets a socket that is no longer connected."""
    reader, writer = await tunnel(net, proxy_port, target, 'domain')
    writer.write(b'payload')
    await writer.drain()
    assert await reader.readexactly(7) == b'payload'
    writer.write_eof()
    await asyncio.wait_for(client_eof.wait(), 3)
    linger = 'hh' if sys.platform == 'win32' else 'ii'
    writer.get_extra_info('socket').setsockopt(
        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(linger, 1, 0))
    writer.transport.abort()
    await asyncio.sleep(.05)
    release.set()


def test_half_close_toward_reset_client_fails_with_routine_enotconn(monkeypatch):
    async def scenario():
        failures, reported = [], asyncio.Event()

        def record(self, error, phase):
            failures.append((error, phase))
            reported.set()

        monkeypatch.setattr(proto.BaseProtocol, 'channel_error', record)
        async with network() as net:
            client_eof, release = asyncio.Event(), asyncio.Event()
            target = await net.listen(lambda r, w: echo_until_released(r, w, client_eof, release))
            await reset_after_half_close(net, await net.proxy(), target, client_eof, release)
            await asyncio.wait_for(reported.wait(), 3)
            await asyncio.sleep(.05)
        return failures

    failures = asyncio.run(asyncio.wait_for(scenario(), 12))
    assert [(type(e), e.errno, phase) for e, phase in failures] == [(OSError, errno.ENOTCONN, 'half_close')]
    assert expected_disconnect(failures[0][0])


def start_diagnostic(log_path, *args):
    return asyncio.create_subprocess_exec(
        sys.executable, '-u', '-m', 'pproxy.diagnostic', '-l', 'socks5://127.0.0.1:0', *args,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'PPROXY_TRACE_PATH': str(log_path), 'PPROXY_TRACE_MODE': 'essential'},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


def test_essential_half_close_toward_reset_client_is_not_logged(tmp_path):
    async def scenario():
        async with network() as net:
            client_eof, release = asyncio.Event(), asyncio.Event()
            target = await net.listen(lambda r, w: echo_until_released(r, w, client_eof, release))
            log_path = tmp_path / 'connections.jsonl'
            process = await start_diagnostic(log_path)
            try:
                line = await asyncio.wait_for(process.stdout.readline(), 3)
                match = re.search(rb'127\.0\.0\.1:(\d+)', line)
                assert match, line
                await reset_after_half_close(net, int(match[1]), target, client_eof, release)
                await asyncio.sleep(.1)
                process.send_signal(signal.SIGTERM)
                _, stderr = await asyncio.wait_for(process.communicate(), 3)
                assert process.returncode == 0, stderr.decode()
                records = [json.loads(line) for line in log_path.read_text().splitlines()]
                assert [r['event'] for r in records] == ['process_start', 'shutdown_requested', 'process_stop'], records
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_handshake_probe_transitions_are_logged_with_reply_code(tmp_path):
    """The TCP connect succeeds but the upstream rejects the probe's CONNECT;
    the probe takes the upstream offline, and that transition must be logged
    with its phase and SOCKS reply, then logged again when it recovers."""
    async def scenario():
        async with network() as net:
            reply = {'code': 4}

            async def upstream(reader, writer):
                assert await reader.readexactly(3) == b'\x05\x01\x00'
                writer.write(b'\x05\x00')
                header = await reader.readexactly(4)
                if header[3] == 3:
                    await reader.readexactly((await reader.readexactly(1))[0] + 2)
                else:
                    await reader.readexactly((4 if header[3] == 1 else 16) + 2)
                writer.write(b'\x05' + bytes([reply['code']]) + b'\x00\x01' + b'\x00' * 6)
                await writer.drain()
                writer.close()

            port = await net.listen(upstream)
            log_path = tmp_path / 'probe.jsonl'
            process = await start_diagnostic(log_path, '-r', f'socks5://127.0.0.1:{port}', '-a', '1')

            async def status(available):
                for _ in range(80):
                    for line in log_path.read_text().splitlines():
                        record = json.loads(line)
                        if record['event'] == 'upstream_status' and record['available'] is available:
                            return record
                    await asyncio.sleep(.05)
                raise AssertionError(log_path.read_text())

            try:
                await asyncio.wait_for(process.stdout.readline(), 3)
                offline = await status(False)
                reply['code'] = 0
                online = await status(True)
                process.send_signal(signal.SIGTERM)
                _, stderr = await asyncio.wait_for(process.communicate(), 3)
                assert process.returncode == 0, stderr.decode()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            records = [json.loads(line) for line in log_path.read_text().splitlines()]
            return port, offline, online, records

    port, offline, online, records = asyncio.run(asyncio.wait_for(scenario(), 12))
    assert offline['bind'] == online['bind'] == f'127.0.0.1:{port}'
    assert offline['phase'] == 'upstream_handshake'
    assert offline['error_type'] == 'Socks5Error' and offline['reply'] == 4
    assert online['phase'] == 'upstream_handshake' and 'error_type' not in online
    assert [r['event'] for r in records if r['event'] == 'upstream_status'] == ['upstream_status'] * 2
    # Failed probes are summarized by the transition, not logged per attempt.
    assert not [r for r in records if r['event'] == 'upstream_handshake_error']


def test_connect_probe_failure_is_logged_once_with_connect_phase(tmp_path):
    closed = socket.socket()
    closed.bind(('127.0.0.1', 0))
    port = closed.getsockname()[1]
    closed.close()

    async def scenario():
        log_path = tmp_path / 'probe.jsonl'
        process = await start_diagnostic(log_path, '-r', f'socks5://127.0.0.1:{port}', '-a', '1')
        try:
            await asyncio.wait_for(process.stdout.readline(), 3)
            await asyncio.sleep(2.5)
            process.send_signal(signal.SIGTERM)
            _, stderr = await asyncio.wait_for(process.communicate(), 3)
            assert process.returncode == 0, stderr.decode()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return [json.loads(line) for line in log_path.read_text().splitlines()]

    records = asyncio.run(asyncio.wait_for(scenario(), 12))
    statuses = [r for r in records if r['event'] == 'upstream_status']
    assert len(statuses) == 1, records
    assert statuses[0]['available'] is False and statuses[0]['phase'] == 'upstream_connect'
    assert statuses[0]['error_type'] == 'ConnectionRefusedError'
    assert not [r for r in records if r['event'] == 'upstream_connect_error']


def test_assertion_location_without_messages_source_or_locals():
    password = 'private-password-must-not-appear'
    try:
        expected_line = inspect.currentframe().f_lineno + 1
        assert False, password
    except AssertionError as error:
        fields = failure_metadata(error)
    frame = fields['traceback'][-1]
    assert frame == {
        'file': __file__, 'line': expected_line,
        'function': 'test_assertion_location_without_messages_source_or_locals',
    }
    assert fields['error_type'] == 'AssertionError'
    assert fields['expected_disconnect'] is False
    assert password not in json.dumps(fields)
    assert 'locals' not in frame and 'source' not in frame


def test_wrapped_assertion_is_not_hidden_as_normal_disconnect():
    try:
        try:
            assert False, 'private-auth-value'
        except AssertionError as cause:
            raise Exception('Connection closed') from cause
    except Exception as error:
        fields = failure_metadata(error)
    assert fields['exception_chain'][0]['error_type'] == 'AssertionError'
    assert fields['exception_chain'][0]['traceback'][-1]['line'] > 0
    assert fields['expected_disconnect'] is False
    assert LogPolicy().select('client_handshake_error', fields, {}) is not None
    assert 'private-auth-value' not in json.dumps(fields)


def test_normal_eof_is_still_silent_and_partial_bytes_are_not_logged():
    try:
        try:
            raise asyncio.IncompleteReadError(b'private-payload', 80)
        except asyncio.IncompleteReadError:
            raise Exception('Connection closed')
    except Exception as error:
        fields = failure_metadata(error)
    assert fields['expected_disconnect'] is True
    assert LogPolicy().select('client_handshake_error', fields, {}) is None
    assert 'private-payload' not in json.dumps(fields)


def test_exception_metadata_never_formats_exception_and_bounds_traceback():
    class PrivateError(Exception):
        errno = 'private-error-number'

        def __str__(self):
            raise AssertionError('must not format an exception')

    def recurse(depth):
        if depth:
            return recurse(depth - 1)
        raise PrivateError('private-message')

    try:
        recurse(40)
    except PrivateError as error:
        fields = failure_metadata(error)
        error.__context__ = error
        cyclic = failure_metadata(error)
    assert len(fields['traceback']) == 32
    assert fields['omitted_outer_frames'] > 0
    assert fields['traceback'][-1]['function'].endswith('recurse')
    assert fields['errno'] is None
    assert 'private-message' not in json.dumps(fields)
    assert 'private-error-number' not in json.dumps(fields)
    assert cyclic['exception_chain_truncated'] is True


@pytest.mark.parametrize('kind', ['assertion', 'invalid_password'])
def test_diagnostic_cli_records_real_handshake_failure_without_credentials(tmp_path, kind):
    async def scenario():
        log_path = tmp_path / 'connections.jsonl'
        username, password = b'private-user-marker', b'private-password-marker'
        wrong = b'private-wrong-password-marker'
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-u', '-m', 'pproxy.diagnostic', '-l',
            'socks5://127.0.0.1:0#' + username.decode() + ':' + password.decode(),
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PPROXY_TRACE_PATH': str(log_path),
                 'PPROXY_TRACE_MODE': 'essential'},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        writers = []
        try:
            line = await asyncio.wait_for(process.stdout.readline(), 3)
            match = re.search(rb'127\.0\.0\.1:(\d+)', line)
            assert match, line
            port = int(match[1])
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writers.append(writer)
            writer.write(b'\x05\x01\x02')
            await writer.drain()
            assert await reader.readexactly(2) == b'\x05\x02'
            version = b'\x02' if kind == 'assertion' else b'\x01'
            writer.write(version + bytes([len(username)]) + username
                         + bytes([len(wrong)]) + wrong)
            await writer.drain()
            try:
                assert await reader.read() == b''
            except ConnectionResetError:
                assert kind == 'assertion'

            # An invalid handshake must not prevent a later authenticated
            # client from establishing and using an ordinary TCP tunnel.
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writers.append(writer)
            writer.write(b'\x05\x01\x02')
            await writer.drain()
            assert await reader.readexactly(2) == b'\x05\x02'
            writer.write(b'\x01' + bytes([len(username)]) + username
                         + bytes([len(password)]) + password)
            await writer.drain()
            assert await reader.readexactly(2) == b'\x01\x00'
            address = proto.socks5_address('echo', 1)
            writer.write(b'\x05\x01\x00' + address)
            await writer.drain()
            assert await reader.readexactly(3 + len(address)) == b'\x05\x00\x00' + address
            writer.write(b'healthy')
            await writer.drain()
            assert await reader.readexactly(7) == b'healthy'
            process.send_signal(signal.SIGTERM)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
            assert process.returncode == 0, stderr.decode()
            text = log_path.read_text()
            records = [json.loads(line) for line in text.splitlines()]
            failures = [r for r in records if r['event'] == 'client_handshake_error']
            assert len(failures) == 1
            failure = failures[0]
            assert failure['error_type'] == ('AssertionError' if kind == 'assertion' else 'Exception')
            assert failure['phase'] == 'client_handshake'
            frame = failure['traceback'][-1]
            assert frame['file'] == proto.__file__
            assert frame['function'] == 'Socks5.accept'
            source = Path(frame['file']).read_text().splitlines()[frame['line'] - 1]
            assert ('Unknown SOCKS auth' if kind == 'assertion' else 'Unauthorized SOCKS') in source
            assert records[0]['event'] == 'process_start'
            assert 'diagnostic.py' in records[0]['source']
            assert records[-1]['event'] == 'process_stop'
            combined = text + stdout.decode() + stderr.decode()
            for secret in (username, password, wrong):
                assert secret.decode() not in combined
        finally:
            for writer in writers:
                writer.close()
            await asyncio.gather(*(w.wait_closed() for w in writers), return_exceptions=True)
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(asyncio.wait_for(scenario(), 12))


@pytest.mark.parametrize('closure', ['eof', 'client_reset', 'upstream_reset'])
def test_essential_transfer_and_peer_closure_produce_no_connection_logs(tmp_path, closure):
    async def scenario():
        async with network() as net:
            remote = asyncio.Future()

            async def echo(reader, writer):
                remote.set_result(writer)
                try:
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                finally:
                    writer.close()

            target = await net.listen(echo)
            log_path = tmp_path / 'connections.jsonl'
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', '-m', 'pproxy.diagnostic',
                '-l', 'socks5://127.0.0.1:0',
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'PPROXY_TRACE_PATH': str(log_path),
                     'PPROXY_TRACE_MODE': 'essential'},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                line = await asyncio.wait_for(process.stdout.readline(), 3)
                match = re.search(rb'127\.0\.0\.1:(\d+)', line)
                assert match, line
                reader, writer = await tunnel(net, int(match[1]), target, 'domain')
                payload = bytes(range(256)) * 1024
                writer.write(payload)
                await writer.drain()
                assert await reader.readexactly(len(payload)) == payload
                if closure == 'eof':
                    writer.write_eof()
                    assert await reader.read() == b''
                else:
                    broken = writer if closure == 'client_reset' else await remote
                    linger = 'hh' if sys.platform == 'win32' else 'ii'
                    broken.get_extra_info('socket').setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(linger, 1, 0))
                    broken.transport.abort()
                    if closure == 'upstream_reset':
                        try:
                            assert await reader.read() == b''
                        except ConnectionResetError:
                            pass
                await asyncio.sleep(.05)
                process.send_signal(signal.SIGTERM)
                _, stderr = await asyncio.wait_for(process.communicate(), 3)
                assert process.returncode == 0, stderr.decode()
                events = [json.loads(line)['event'] for line in log_path.read_text().splitlines()]
                assert events == ['process_start', 'shutdown_requested', 'process_stop']
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    asyncio.run(asyncio.wait_for(scenario(), 12))


@pytest.mark.parametrize('phase', ['relay', 'http_relay', 'half_close'])
def test_relay_reports_unexpected_errors_at_exception_boundary(phase):
    class Reader:
        def at_eof(self):
            return False

        async def read(self, count):
            raise AssertionError('private-payload')

    class Writer:
        closed = False

        def is_closing(self):
            return self.closed

        def close(self):
            self.closed = True

        def can_write_eof(self):
            return True

        def write_eof(self):
            raise AssertionError('private-payload')

    async def scenario():
        protocol = proto.HTTP(None) if phase == 'http_relay' else proto.Socks5(None)
        failures = []
        protocol.channel_error = lambda error, where: failures.append((error, where))
        reader, writer = Reader(), Writer()
        if phase == 'half_close':
            result = await protocol.finish_channel(writer, True)
        else:
            relay = protocol.http_channel if phase == 'http_relay' else protocol.channel
            result = await relay(reader, writer, lambda n: None, lambda n: None)
        assert result is False and writer.closed
        assert len(failures) == 1 and failures[0][1] == phase
        assert isinstance(failures[0][0], AssertionError)
    asyncio.run(scenario())


def test_truncated_upstream_handshake_is_logged_as_connection_failure(tmp_path):
    async def scenario():
        async with network() as net:
            async def reject(reader, writer):
                await reader.readexactly(3)
                writer.close()

            upstream = await net.listen(reject)
            log_path = tmp_path / 'upstream.jsonl'
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', '-m', 'pproxy.diagnostic',
                '-l', 'socks5://127.0.0.1:0', '-r', f'socks5://127.0.0.1:{upstream}',
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'PPROXY_TRACE_PATH': str(log_path),
                     'PPROXY_TRACE_MODE': 'essential'},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                line = await asyncio.wait_for(process.stdout.readline(), 3)
                match = re.search(rb'127\.0\.0\.1:(\d+)', line)
                assert match, line
                reader, writer = await net.tcp(int(match[1]))
                writer.write(b'\x05\x01\x00')
                await writer.drain()
                assert await reader.readexactly(2) == b'\x05\x00'
                writer.write(b'\x05\x01\x00' + proto.socks5_address('unused.test', 443))
                await writer.drain()
                assert (await reader.readexactly(10))[1] != 0
                assert await reader.read() == b''
                process.send_signal(signal.SIGTERM)
                _, stderr = await asyncio.wait_for(process.communicate(), 3)
                assert process.returncode == 0, stderr.decode()
                records = [json.loads(line) for line in log_path.read_text().splitlines()]
                failures = [r for r in records if r['event'] == 'upstream_handshake_error']
                assert len(failures) == 1
                assert failures[0]['error_type'] == 'IncompleteReadError'
                assert failures[0]['phase'] == 'upstream_handshake'
                assert failures[0]['traceback']
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    asyncio.run(asyncio.wait_for(scenario(), 12))

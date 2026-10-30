"""Real native rule evaluation and SOCKS peers; no installed provider required."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import pytest

from pproxy import capture, process_rules, server
from test_proxy_stability import echo, network, run
from test_socks5_udp import datagram, proxy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    if sys.platform != 'darwin' or not shutil.which('xcrun'):
        pytest.skip('macOS SDK required for native process rule/wire harness')
    folder = tmp_path_factory.mktemp('process-native')
    binary = folder / 'language_server'
    command = ['xcrun', 'swiftc', '-swift-version', '5', '-module-cache-path', str(folder / 'modules'),
               *map(str, sorted((ROOT / 'macos/Shared').glob('*.swift'))),
               str(ROOT / 'macos/Tests/main.swift'), '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    return binary


def native_call(native, value, success=True):
    result = subprocess.run([str(native)], input=json.dumps(value), capture_output=True, text=True, timeout=10)
    if success:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert result.returncode != 0


async def native_peer(native, value):
    process = await asyncio.create_subprocess_exec(str(native), stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(value).encode()), 8)
        assert process.returncode == 0, stderr.decode()
        return json.loads(stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_all_requested_process_patterns(native):
    identities = [
        ('Antigravity.app', {'executable': '/Applications/Antigravity.app/Contents/Resources/app/language_server'}),
        ('Antigravity', {'executable': '/Applications/Antigravity.app/Contents/MacOS/Antigravity'}),
        ('com.google.antigravity', {'identifier': 'com.google.antigravity'}),
        ('language_server', {'executable': '/opt/tools/language_server'}),
        ('agy', {'executable': '/opt/homebrew/bin/agy'}),
        ('/Applications/Antigravity.app/*', {'executable': '/Applications/Antigravity.app/Contents/Helpers/helper'}),
        ('language_*', {'executable': '/opt/tools/language_server_macos_arm64'}),
        ('com.google.antigravity', {'identifier': 'helper', 'applicationIdentifiers': ['com.google.antigravity']}),
    ]
    for pattern, identity in identities:
        rules = process_rules.normalize({'rules': [{'processes': [pattern], 'action': 'proxy'}]})
        result = native_call(native, {'operation': 'rules', 'rules': rules, 'cases': [
            dict(identity, protocol='tcp'), dict(identity, protocol='udp'), {'executable': '/usr/bin/curl'}]})
        assert [r['action'] for r in result] == ['proxy', 'proxy', 'direct'], pattern
    actual = native_call(native, {'operation': 'identity'})
    assert 'language_server' in actual['candidates']
    assert str(native).lower() in actual['candidates']
    assert actual['pid'] > 1


def test_ordered_rules_and_each_udp_destination(native):
    rules = process_rules.normalize({'rules': [
        {'processes': ['*'], 'hosts': ['10.0.0.0/8'], 'action': 'direct'},
        {'processes': ['agy'], 'hosts': ['2001:db8::/32'], 'ports': ['440-445'], 'protocol': 'udp', 'action': 'block'},
        {'processes': ['agy'], 'hosts': ['*.example.com'], 'ports': [443], 'action': 'proxy'},
        {'processes': ['agy'], 'enabled': False, 'action': 'block'},
        {'processes': ['agy'], 'action': 'proxy'},
    ]})
    cases = [dict(executable='/bin/agy', host=h, port=p, protocol=proto, **extra) for h, p, proto, extra in [
        ('10.0.0.1', 443, 'udp', {}), ('2001:db8::12', 443, 'udp', {}),
        ('2001:db8::12', 443, 'tcp', {}), ('203.0.113.1', 443, 'udp', {'hostname': 'example.com'}),
        ('203.0.113.1', 8443, 'udp', {})]]
    result = native_call(native, {'operation': 'rules', 'rules': rules, 'cases': cases})
    assert [r['action'] for r in result] == ['direct', 'block', 'proxy', 'proxy', 'proxy']
    assert all(r['udpIntercept'] for r in result)  # a later datagram may take another route


@pytest.mark.parametrize('value', [
    {'rules': [{'processes': [], 'action': 'proxy'}]},
    {'rules': [{'processes': ['agy'], 'action': 'typo'}]},
    {'rules': [{'processes': ['agy'], 'action': 'proxy', 'ports': ['9000-8000']}]},
    {'rules': [{'processes': ['agy'], 'action': 'proxy', 'ports': [65536]}]},
    {'rules': [{'processes': ['agy'], 'action': 'proxy', 'hosts': ['10.0.0.0/99']}]},
    {'rules': [{'processes': ['agy'], 'action': 'proxy', 'protocol': 'quic'}]},
    {'rules': [{'processes': ['agy'], 'action': 'proxy', 'enabled': 'false'}]},
    {'rules': [{'proceses': ['agy'], 'action': 'proxy'}]},
])
def test_invalid_rules_fail(value):
    with pytest.raises(ValueError):
        process_rules.normalize(value)


def test_semicolon_processes():
    rules = process_rules.normalize({'rules': [{'processes': '"Antigravity.app"; "Antigravity"; com.google.antigravity;language_server; "agy"', 'action': 'proxy'}]})
    assert rules['rules'][0]['processes'] == ['Antigravity.app', 'Antigravity', 'com.google.antigravity', 'language_server', 'agy']


def test_listener_uses_bound_port_and_auth():
    async def case():
        listener = server.proxies_by_uri('http+socks5://127.0.0.1:0#user:password')
        sockets = await listener.start_server({})
        try:
            config = capture.listener_configuration([(listener, sockets)])
            assert config == {'host': '127.0.0.1', 'port': sockets.sockets[0].getsockname()[1],
                              'username': 'user', 'password': 'password'}
            with pytest.raises(ValueError):
                capture.listener_configuration([(listener, sockets), (listener, sockets)])
        finally:
            sockets.close()
            await sockets.wait_closed()
    run(case())


@pytest.mark.parametrize('mode', ['tcp', 'udp'])
def test_native_socks_through_two_python_proxies(native, mode):
    async def case():
        async with network() as net, datagram(echo=True) as udp:
            tcp = await net.listen(echo)
            remote_port = await proxy(net)
            upstream = server.proxies_by_uri('socks5://127.0.0.1:%s' % remote_port)
            local_port = await proxy(net, remotes=[upstream], tcp_remotes=[upstream], users=[b'user:pass'])
            payload = os.urandom(4096)  # larger than a QUIC Initial or typical media packet
            try:
                result = await native_peer(native, dict(operation=mode, proxyPort=local_port,
                    host='127.0.0.1', port=tcp if mode == 'tcp' else udp.addr[1],
                    username='user', password='pass', payload=base64.b64encode(payload).decode()))
                assert base64.b64decode(result['data']) == payload
                if mode == 'udp':
                    assert (result['host'], result['port']) == udp.addr
            finally:
                await upstream.udp_close()
    run(case())


def test_native_connect_preserves_banner_after_ipv6_reply(native):
    async def case():
        async with network() as net:
            async def peer(reader, writer):
                assert await reader.readexactly(3) == b'\x05\x01\x00'
                writer.write(b'\x05\x00')
                await writer.drain()
                assert (await reader.readexactly(10))[:4] == b'\x05\x01\x00\x01'
                writer.write(b'\x05\x00\x00\x04' + b'\0' * 15 + b'\1\x00\x50' + b'banner')
                await writer.drain()
                await reader.read()
                writer.close()
            port = await net.listen(peer)
            result = await native_peer(native, dict(operation='tcp', proxyPort=port, host='127.0.0.1',
                                      port=80, payload='', replyLength=6))
            assert base64.b64decode(result['data']) == b'banner'
    run(case())


@pytest.mark.parametrize('fragment', [0, 1])
def test_native_udp_zero_length_and_fragment(native, fragment):
    packet = bytes([0, 0, fragment, 1, 127, 0, 0, 1, 0, 53])
    result = native_call(native, {'operation': 'decode', 'packet': base64.b64encode(packet).decode()})
    assert result == ({'data': '', 'host': '127.0.0.1', 'port': 53} if fragment == 0 else {'dropped': True})


@pytest.mark.parametrize('unexpected_exit', [False, True])
def test_actual_cli_listener_and_controller_lifecycle(tmp_path, unexpected_exit):
    """Only the OS controller is doubled; run the actual pproxy CLI and listener."""
    if sys.platform != 'darwin':
        pytest.skip('capture launcher is macOS-only')
    app = tmp_path / 'ControllerBoundaryDouble.app'
    executable = app / 'Contents/MacOS/PythonProxy'
    executable.parent.mkdir(parents=True)
    resources = app / 'Contents/Resources'
    resources.mkdir()
    (resources / 'build.json').write_text('{"signed": true}')
    state = tmp_path / 'config.json'
    stopped = tmp_path / 'stopped'
    executable.write_text('#!' + sys.executable + '\n' + '''
import json, os, signal, sys, time
from pathlib import Path
config = json.load(sys.stdin)
assert config['ownerPID'] == os.getppid()
assert config['port'] > 0
Path(%r).write_text(json.dumps(config))
def stop(*args):
    Path(%r).write_text('stopped')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print('Process TCP/UDP capture active.', flush=True)
if %r:
    time.sleep(0.2)
    sys.exit(7)
while True:
    signal.pause()
''' % (str(state), str(stopped), unexpected_exit))
    executable.chmod(0o755)
    rules = tmp_path / 'rules.json'
    rules.write_text('{"rules":[{"processes":["language_server"],"action":"proxy"}]}')

    async def case():
        async with network() as net:
            echo_port = await net.listen(echo)
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', '-m', 'pproxy', '-l', 'socks5://127.0.0.1:0',
                '--process-rules', str(rules), '--capture-app', str(app),
                cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
            try:
                await asyncio.wait_for(process.stdout.readuntil(b'Process TCP/UDP capture active.\n'), 5)
                config = json.loads(state.read_text())
                assert config['rules'] == process_rules.load(rules)
                assert config['ownerPID'] == process.pid
                if not unexpected_exit:
                    reader, writer = await net.tcp(config['port'])
                    writer.write(b'\x05\x01\x00')
                    await writer.drain()
                    assert await reader.readexactly(2) == b'\x05\x00'
                    writer.write(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + echo_port.to_bytes(2, 'big'))
                    await writer.drain()
                    assert (await reader.readexactly(10))[:2] == b'\x05\x00'
                    writer.write(b'lifecycle')
                    await writer.drain()
                    assert await reader.readexactly(9) == b'lifecycle'
                    writer.close()
                    await writer.wait_closed()
                    process.terminate()
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                assert process.returncode == (1 if unexpected_exit else 0), (stdout, stderr)
                if unexpected_exit:
                    assert b'stopped unexpectedly (exit 7)' in stderr
                else:
                    assert stopped.read_text() == 'stopped'
                with pytest.raises(OSError):
                    await asyncio.open_connection('127.0.0.1', config['port'])
            finally:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
    run(case())


def test_native_controller_reads_large_configuration(tmp_path):
    if sys.platform != 'darwin' or not shutil.which('xcrun'):
        pytest.skip('macOS SDK required for native controller')
    binary = tmp_path / 'controller'
    result = subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-module-cache-path', str(tmp_path / 'modules'),
                             *map(str, sorted((ROOT / 'macos/Shared').glob('*.swift'))),
                             str(ROOT / 'macos/Controller/main.swift'), '-framework', 'SystemExtensions',
                             '-framework', 'NetworkExtension', '-o', str(binary)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    rules = process_rules.normalize({'rules': [
        {'processes': ['/Applications/' + 'x' * 100 + str(i) + '.app'], 'action': 'proxy'} for i in range(500)]})
    data = json.dumps(dict(host='127.0.0.1', port=8082, ownerPID=os.getpid(), udpIdle=120, rules=rules)).encode()
    assert len(data) > 65536
    result = subprocess.run([str(binary), 'check-config'], input=data, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert b'Configuration valid' in result.stdout
    # A range typo must not silently turn a selected flow into a default DIRECT.
    value = json.loads(data)
    value['rules']['rules'][0]['ports'] = ['9000-8000']
    result = subprocess.run([str(binary), 'check-config'], input=json.dumps(value).encode(), capture_output=True, timeout=10)
    assert result.returncode == 2 and b'Invalid rule port range' in result.stderr

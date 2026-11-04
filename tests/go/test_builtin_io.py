"""Fixed official shard dependency: scalar output and terminal uncaught panic.

https://go.dev/ref/spec#Bootstrapping https://go.dev/ref/spec#Handling_panics
https://go.dev/ref/spec#Terminating_statements https://go.dev/ref/spec#Order_of_evaluation
No defer/recover/unwind, goroutine or general interface-payload acceptance claim.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_diagnostics import original as diagnostic_original
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_frontend import source


OUTPUT = (b'hello, world\n\nA1trueB C -128 255 -32768 65535\n'
          b'-9223372036854775808 18446744073709551615\n'
          b'-2147483648 4294967295 0 0\nfalse nul\x00tail\n123 order 123\n'
          b'-8 multi true\n937 42\n9\n')

# All byte-distinct earlier negative panic inputs remain unchanged.
GRADUATED = [
    (source('panic(1)'), b'panic: 1\n'),
    ('package main\nfunc main(){panic(1)}', b'panic: 1\n'),
    (diagnostic_original(body='panic("unimplemented runtime")'), b'panic: unimplemented runtime\n'),
]
PANICS = GRADUATED + [
    (source('panic(int64(-9223372036854775808))'), b'panic: -9223372036854775808\n'),
    (source('panic(uint64(18446744073709551615))'), b'panic: 18446744073709551615\n'),
    (source('panic(false)'), b'panic: false\n'),
    (source('panic("x\\x00y")'), b'panic: x\x00y\n'),
    (source('panic(payload());println("after")', 'func payload()int{println("before");return 7}'), b'before\npanic: 7\n'),
    (source('_=stop();println("after")', 'func stop()uint64{panic("stop")}'), b'panic: stop\n'),
    (source('var p *uint64;_=read(p)', 'func read(p *uint64)uint64{if p==nil{panic("nil")};return *p}'), b'panic: nil\n'),
    ('package main; func main(){if panic("x"); true {}}', b'panic: x\n'),
    ('package main; func main(){for panic("x"); true; {}}', b'panic: x\n'),
    ('package main; func main(){switch panic("x"); {default:}}', b'panic: x\n'),
    (source('for i:=0;i<1;panic("post"){println("body")}'), b'body\npanic: post\n'),
]


# Named types and multiline panic reports have implementation-specific text.
# Exact complete streams are retained, not normalized into byte equality.
REPORT_DIFFERENCES = [
    (source('panic(N(7))', 'type N int'), b'panic: 7\n', b'panic: main.N(7)\n'),
    (source('panic("first\\nsecond")'), b'panic: first\nsecond\n', b'panic: first\n\tsecond\n'),
]


def ken_original():
    path = Path(__file__).parent / 'upstream/go1.27.3/test/ken/for.go'
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'c7de70084214cdc33220b168d0d47f075c0d2a3dac641fd66b46394079f6400e'
    return raw.decode()


def test_unchanged_original_ken_for_no_longer_stops_at_panic(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(ken_original(), filename='for.go')


@pytest.mark.parametrize('original,expected_stderr', PANICS + [(p, own) for p, own, _ in REPORT_DIFFERENCES])
def test_terminal_panic_is_typed_and_not_an_error_return(original, expected_stderr, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(original)
    text = str(module)
    assert 'syscall' in text and 'unreachable' in text


def test_output_ir_uses_owned_bounded_formatting_and_real_write_retry(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(('builtin_io.go',))[0][1])
    text = str(module)
    assert '__pcc_go_builtin_write_all' in text and '__pcc_go_builtin_integer' in text
    assert 'urem i64' in text and 'udiv i64' in text
    writer = module.globals['__pcc_go_builtin_write_all']
    blocks = {block.name: block for block in writer.blocks}
    calls = [instruction for instruction in blocks['write'].instructions
             if instruction.opname == 'call' and '"syscall"' in instruction.text]
    assert len(calls) == 1
    write_result = calls[0].text.split(' = ', 1)[0]
    compare, branch = blocks['retry'].instructions
    assert compare.opname == 'icmp'
    condition, comparison = compare.text.split(' = ', 1)
    assert comparison == f'icmp eq i64 {write_result}, -4'
    assert branch.opname == 'br' and blocks['retry'].is_terminated
    assert branch.text == f'br i1 {condition}, label %condition, label %epipe'
    assert 'printf' not in text and 'malloc' not in text


INVALID = [
    ('panic()', 'exactly one argument', 'not enough arguments'),
    ('panic(1,2)', 'exactly one argument', 'too many arguments'),
    ('_=print(1)', 'void function call', 'no value'),
    ('x:=println();_=x', 'void function call', 'no value'),
    ('print(nil)', 'untyped nil', 'untyped nil'),
    ('println(9223372036854775808)', 'overflows int', 'overflows'),
    ('panic(18446744073709551616)', 'overflows int', 'overflows'),
]


@pytest.mark.parametrize('body,message,reference_message', INVALID)
def test_builtin_invalid_arguments_remain_real_type_errors(body, message, reference_message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('builtin-io', source('panic(nil)')),
    ('builtin-io', source('var p *int;panic(p)')),
    ('builtin-io', source('var p *int;print(p)')),
    ('builtin-io', source('panic(S{1})', 'type S struct{X int}')),
    ('builtin-io', source('panic(E(1))', 'type E int;func(E)Error()string{println("effect");return "error"}')),
    ('builtin-io', source('panic(S("x"))', 'type S string;func(S)String()string{println("effect");return "string"}')),
    ('predeclared-shadowing', source('print(3)', 'func print(x int){}')),
    ('predeclared-shadowing', source('println(3)', 'func println(x int){}')),
    ('predeclared-shadowing', source('panic(3)', 'func panic(x int){}')),
]


@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_unsupported_payload_and_builtin_shadowing_are_not_silent_dispatch(code, original):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


def test_nonlinux_output_is_an_explicit_target_boundary():
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source('print("x")'), target_triple='arm64-apple-darwin')
    assert caught.value.code == 'go.unsupported.builtin-io'


@pytest.mark.parametrize('statement', ['defer f()', 'go f()'])
def test_terminal_panic_does_not_claim_defer_or_goroutine_support(statement):
    with pytest.raises(GoDiagnostic, match='unsupported statement'):
        compile_go(source(statement+';panic(1)', 'func f(){}'))


def capture(directory, owner, binary, input_hash):
    actual = run(binary)
    (directory / (owner+'.stdout')).write_bytes(actual.stdout)
    (directory / (owner+'.stderr')).write_bytes(actual.stderr)
    (directory / (owner+'.execution.json')).write_text(json.dumps({
        'input_sha256': input_hash, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_sha256': hashlib.sha256(actual.stdout).hexdigest(),
        'stderr_sha256': hashlib.sha256(actual.stderr).hexdigest(),
    }, indent=2)+'\n')
    return actual


@pytest.mark.integration
@pytest.mark.parametrize('original,expected_stderr', PANICS + [(p, own) for p, own, _ in REPORT_DIFFERENCES])
def test_real_terminal_panics_execute_without_returning_or_losing_payload(original, expected_stderr, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('panic.go', original),))
    binary = tmp_path/'panic-native';write_artifact(artifact, binary)
    actual = capture(tmp_path, 'pcc', binary, hashlib.sha256(original.encode()).hexdigest())
    assert actual == Execution(2, b'', expected_stderr)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,expected_stderr', PANICS + [(p, own) for p, own, _ in REPORT_DIFFERENCES])
def test_official_terminal_panic_keeps_full_implementation_specific_streams(original, expected_stderr, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('panic.go', original),))
    native = tmp_path/'panic-native';write_artifact(artifact, native)
    reference = compile_reference((('panic.go', original),), tmp_path/'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    input_hash = hashlib.sha256(original.encode()).hexdigest()
    actual = capture(tmp_path, 'pcc', native, input_hash)
    oracle = capture(tmp_path, 'reference', reference, input_hash)
    (tmp_path/'comparison.json').write_text(json.dumps({
        'comparison': 'uncaught terminal status and scalar payload; NOT identical stderr qualification',
        'raw_streams_identical': actual == oracle,
        'implementation_specific_stderr_difference_retained': actual.stderr != oracle.stderr,
        'full_panic_recover_unwind': 'unimplemented',
    }, indent=2)+'\n')
    assert actual == Execution(2, b'', expected_stderr)
    assert oracle.returncode == 2 and oracle.stdout == b''
    expected_prefix = next((ref for text, own, ref in REPORT_DIFFERENCES if text == original), expected_stderr)
    assert oracle.stderr.startswith(expected_prefix)
    assert (reference.parent/'panic.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name', ['original-ken-for', 'authored-builtin-output'])
def test_complete_original_and_output_programs_match_full_raw_streams(name, tmp_path, monkeypatch):
    originals = (('for.go', ken_original()),) if name == 'original-ken-for' else units(('builtin_io.go',))
    expected = Execution(0, b'', b'' if name == 'original-ken-for' else OUTPUT)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    native = tmp_path/'program-native';write_artifact(artifact, native)
    reference = compile_reference(originals, tmp_path/'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    source_hash = hashlib.sha256(originals[0][1].encode()).hexdigest()
    assert capture(tmp_path, 'pcc', native, source_hash) == capture(tmp_path, 'reference', reference, source_hash) == expected
    assert (reference.parent/originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_builtin_inputs(body, message, reference_message, tmp_path):
    original = source(body)
    with pytest.raises(GoDiagnostic, match=message):compile_go(original)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path/'reference',
                                          compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
                                          diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path/'reference/invalid.go').read_bytes() == original.encode()
    assert not (tmp_path/'reference/oracle').exists()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_official_go_builds_unsupported_builtin_originals_without_execution(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path/'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file() and (reference.parent/'original.go').read_bytes() == original.encode()


def rune_frontier():
    raw = (Path(__file__).parent/'fixtures/builtin_io_rune_frontier.go').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '47df041a6b700a632c7caedba07fc5c3a7ca7ed35ce178d4e98d12ed793a3c1e'
    return raw.decode()


def test_original_rune_fixture_graduates_unchanged_to_owned_ir(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(rune_frontier(), 'original.go')


@pytest.mark.integration
def test_original_rune_fixture_executes_owned_native_with_complete_streams(tmp_path, monkeypatch):
    original = rune_frontier()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('original.go', original),))
    native = tmp_path / 'rune-native'
    write_artifact(artifact, native)
    digest = hashlib.sha256(original.encode()).hexdigest()
    assert capture(tmp_path, 'pcc', native, digest) == Execution(0, b'', OUTPUT)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_retained_rune_fixture_matches_official_complete_streams(tmp_path, monkeypatch):
    original = rune_frontier()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('original.go', original),))
    native = tmp_path / 'rune-native'
    write_artifact(artifact, native)
    reference = compile_reference((('original.go', original),), tmp_path/'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    digest = hashlib.sha256(original.encode()).hexdigest()
    assert capture(tmp_path, 'pcc', native, digest) == capture(tmp_path, 'reference', reference, digest) == Execution(0, b'', OUTPUT)
    assert (reference.parent/'original.go').read_bytes() == original.encode()


def capture_broken_stderr(directory, owner, binary, input_hash):
    reader, writer = os.pipe()
    os.close(reader)
    try:
        result = subprocess.run([str(binary)], cwd=binary.parent, stdout=subprocess.PIPE,
                                stderr=writer, timeout=5, restore_signals=True)
    finally:
        os.close(writer)
    (directory/(owner+'.stdout')).write_bytes(result.stdout)
    (directory/(owner+'.execution.json')).write_text(json.dumps({
        'input_sha256': input_hash, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': result.returncode, 'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
        'stderr_transport': 'real pipe with all read ends closed; bytes undeliverable, not captured',
        'initial_sigpipe': 'subprocess restore_signals=True',
    }, indent=2)+'\n')
    return result.returncode, result.stdout


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,exit_code', [('println("x")', 0), ('panic("x")', 2)])
def test_real_broken_stderr_matches_official_terminal_status(body, exit_code, tmp_path, monkeypatch):
    original = source(body)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('broken.go', original),))
    native = tmp_path/'native';write_artifact(artifact, native)
    reference = compile_reference((('broken.go', original),), tmp_path/'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    digest = hashlib.sha256(original.encode()).hexdigest()
    assert capture_broken_stderr(tmp_path, 'pcc', native, digest) == (exit_code, b'')
    assert capture_broken_stderr(tmp_path, 'reference', reference, digest) == (exit_code, b'')


@pytest.mark.integration
@pytest.mark.parametrize('mask,pending,disposition', [
    (0, False, 0), (0, False, 1), (1 << 9, False, 0),
    (1 << 12, False, 0), ((1 << 12) | (1 << 9), True, 0),
])
def test_owned_writer_preserves_calling_thread_signal_state(mask, pending, disposition, tmp_path, monkeypatch):
    # ABI observer only: initialize/query Linux signal state around the actual
    # compiled Go function. It does not replace writes or implement Go output.
    from pcc.frontends.go import DEFAULT_TARGET, GO_MAIN_SYMBOL
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from tests.go.owned_execution import Artifact

    original = source('println("x");print("y")')
    (tmp_path/'original.go').write_text(original)
    # Named slots, bytes relative to rsp. Kernel mask=8, sigaction=32 bytes.
    selected_mask, observed_mask, observed_pending = 0, 8, 16
    before_action, after_action, selected_action = 32, 64, 96
    assembly = '.intel_syntax noprefix\n.text\n.globl _start\n_start:\n  sub rsp, 144\n'
    assembly += f'  mov qword ptr [rsp+{selected_mask}], {mask}\n'
    for index, value in enumerate((disposition, 0, 0, 0)):
        assembly += f'  mov qword ptr [rsp+{selected_action+8*index}], {value}\n'
    assembly += (
        f'  mov rax, 14\n  mov rdi, 2\n  lea rsi, [rsp+{selected_mask}]\n  xor rdx, rdx\n  mov r10, 8\n  syscall\n  test rax, rax\n  jne failed\n'
        f'  mov rax, 13\n  mov rdi, 13\n  lea rsi, [rsp+{selected_action}]\n  xor rdx, rdx\n  mov r10, 8\n  syscall\n  test rax, rax\n  jne failed\n'
        f'  mov rax, 13\n  mov rdi, 13\n  xor rsi, rsi\n  lea rdx, [rsp+{before_action}]\n  mov r10, 8\n  syscall\n  test rax, rax\n  jne failed\n'
    )
    if pending:
        assembly += '  mov rax, 39\n  syscall\n  mov rdi, rax\n  mov rax, 62\n  mov rsi, 13\n  syscall\n  test rax, rax\n  jne failed\n'
    assembly += (
        f'  call {GO_MAIN_SYMBOL}\n'
        f'  mov rax, 14\n  xor rdi, rdi\n  xor rsi, rsi\n  lea rdx, [rsp+{observed_mask}]\n  mov r10, 8\n  syscall\n  test rax, rax\n  jne failed\n'
        f'  mov rax, [rsp+{observed_mask}]\n  cmp rax, {mask}\n  jne failed\n'
        f'  mov rax, 127\n  lea rdi, [rsp+{observed_pending}]\n  mov rsi, 8\n  syscall\n  test rax, rax\n  jne failed\n'
        f'  mov rax, [rsp+{observed_pending}]\n  and rax, 4096\n  cmp rax, {4096 if pending else 0}\n  jne failed\n'
        f'  mov rax, 13\n  mov rdi, 13\n  xor rsi, rsi\n  lea rdx, [rsp+{after_action}]\n  mov r10, 8\n  syscall\n  test rax, rax\n  jne failed\n'
    )
    for index in range(4):
        assembly += f'  mov rax, [rsp+{before_action+8*index}]\n  cmp rax, [rsp+{after_action+8*index}]\n  jne failed\n'
    assembly += '  xor rdi, rdi\n  jmp finish\nfailed:\n  mov rdi, 90\nfinish:\n  mov rax, 60\n  syscall\n'
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        text = str(compile_go(original))
        obj = emit_owned_object(text, DEFAULT_TARGET)
        executable = link_static_executable([assemble_file(assembly), parse_relocatable(obj)], entry='_start')
    (tmp_path/'observer.s').write_text(assembly)
    binary = tmp_path/'observer';write_artifact(Artifact(text, obj, executable), binary)
    assert capture_broken_stderr(tmp_path, 'pcc', binary, hashlib.sha256(original.encode()).hexdigest()) == (0, b'')

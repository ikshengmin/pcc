"""Original Go source -> PCC IR -> self backend -> owned Linux ELF execution.

Host Python runs the compiler. This is not native pcc1 qualification. The
startup contains no source-language semantics; it calls main or observes the
full int64 Result function. The optional official Go oracle is never a fallback.
"""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess

from pcc.frontends.go import DEFAULT_TARGET, GO_MAIN_SYMBOL, compile_go_sources, function_symbol


@dataclass(frozen=True)
class Artifact:
    ir: str
    object: bytes
    executable: bytes
    owner: str = "host-python/pcc-go/pcc-self/pcc-elf"


@dataclass(frozen=True)
class Execution:
    returncode: int
    stdout: bytes
    stderr: bytes


def compile_owned(units, *, observe_result=False, observe_entry=None):
    return compile_module(compile_go_sources(units, target_triple=DEFAULT_TARGET),
                          observe_result=observe_result, observe_entry=observe_entry)


def compile_module(module, *, observe_result=False, observe_entry=None):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file

    if observe_result and observe_entry is not None:
        raise ValueError("select one Go ABI observer mode")
    entry = function_symbol("Result") if observe_result else GO_MAIN_SYMBOL
    arguments, output_bytes = (), 8 if observe_result else 0
    if observe_entry is not None:
        name, arguments = observe_entry
        entry = function_symbol(name)
    if observe_result or observe_entry is not None:
        result = module.globals.get(entry)
        width = getattr(result.function_type.return_type, "width", 0) if result else 0
        if result is None or width not in (8, 16, 32, 64) or len(result.args) != len(arguments):
            raise ValueError("Go observer requires a fixed-width scalar result and matching integer arguments")
        if observe_result and (width != 64 or arguments):
            raise ValueError("Go Result observer requires an int64 result without arguments")
        if len(arguments) > 6 or any(getattr(arg.type, "width", 0) not in (8, 16, 32, 64) for arg in result.args):
            raise ValueError("Go scalar test observer admits at most six integer arguments")
        output_bytes = width // 8
    ir_text = str(module)
    obj = emit_owned_object(ir_text, DEFAULT_TARGET)
    assembly = ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n  sub rsp, 16\n"
    for register, argument in zip(("rdi", "rsi", "rdx", "rcx", "r8", "r9"), arguments):
        # Argument setup conveys bits only. It does not implement any Go
        # conversion; the original typed function performs those operations.
        bits = int(argument) & ((1 << 64) - 1)
        signed_bits = bits if bits < (1 << 63) else bits - (1 << 64)
        assembly += f"  mov {register}, {signed_bits}\n"
    assembly += f"  call {entry}\n"
    if output_bytes:
        assembly += (
            "  mov qword ptr [rsp], rax\n"
            f"  mov rax, 1\n  mov rdi, 1\n  mov rsi, rsp\n  mov rdx, {output_bytes}\n  syscall\n"
        )
    assembly += "  mov rax, 60\n  mov rdi, 0\n  syscall\n"
    executable = link_static_executable([assemble_file(assembly), parse_relocatable(obj)], entry="_start")
    return Artifact(ir_text, obj, executable)


def write_artifact(artifact, destination):
    with destination.open("xb") as stream:
        stream.write(artifact.executable)
    destination.chmod(0o700)


def run(binary):
    result = subprocess.run([str(binary)], capture_output=True, timeout=5, cwd=binary.parent)
    return Execution(result.returncode, result.stdout, result.stderr)


class ReferenceBuildFailure(RuntimeError):
    """An oracle/tool/protocol failure, never evidence of source rejection."""

    def __init__(self, kind, receipt):
        self.kind = kind
        self.receipt = receipt
        super().__init__(f"official Go reference {kind}; raw receipt: {receipt}")


def _source_messages(stderr, sources, directory):
    """Fail closed on mixed source/tool errors, including deferred cache failure.

    Go build uses '# package' headers. Its positioned source diagnostics can
    have positioned, tab-indented suberrors, or tab-indented have/want argument
    lists. No other unpositioned stderr is semantic-rejection evidence.
    """
    source_paths = {path.resolve() for path in sources}
    messages, unexpected = [], []
    source_attached = False
    for line in stderr.decode('utf-8', 'replace').splitlines():
        if not line:
            continue
        if line.startswith('# '):
            source_attached = False
            continue
        match = re.match(r'^(.+?):[0-9]+(?::[0-9]+)?:\s*(.*)$', line.lstrip('\t'))
        if match is not None:
            location = Path(match[1])
            if not location.is_absolute():
                location = directory / location
            source_attached = location.resolve() in source_paths
            if source_attached:
                messages.append(match[2])
            else:
                unexpected.append(line)
        elif source_attached and re.match(r'^\t+(?:have|want) \(.*\)$', line):
            continue
        else:
            source_attached = False
            unexpected.append(line)
    return messages, unexpected


def _resolve_reference_compiler(compiler, directory, env):
    """Resolve once under the same cwd/PATH rules as the child process."""
    requested = os.fspath(compiler)
    if os.path.dirname(requested):
        path = Path(requested)
        return (path if path.is_absolute() else directory / path).resolve()
    for entry in os.get_exec_path(env):
        folder = Path(entry)
        if not folder.is_absolute():
            folder = directory / folder
        candidate = folder / requested
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise FileNotFoundError(f'reference compiler not found on child PATH: {requested}')


def _reference_build(paths, sources, directory, *, compiler, cache, gopath=None, diagnostics=None):
    """One observed process, with success and semantic-rejection paths distinct.

    A normal source rejection requires rc1, source-attributed case diagnostics,
    no stdout or produced binary, and no crash/resource/dependency indicators.
    Every failure (including spawn/timeout/missing output) retains raw streams
    and a receipt before it raises; callers cannot substitute arbitrary build
    failure for invalid-Go agreement.
    """
    directory = directory.resolve()
    binary = directory / 'oracle'
    env = dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off',
               GOWORK='off', GO111MODULE='off', CGO_ENABLED='0', GOMAXPROCS='2',
               GOCACHE=str(cache))
    if gopath is not None:
        env['GOPATH'] = str(gopath.resolve())
    command = [str(compiler), 'build', '-p=2', '-o', str(binary), *map(str, paths)]
    receipt_path = directory / 'build.execution.json'
    receipt = {'command': command, 'requested_compiler': os.fspath(compiler), 'cwd': str(directory),
               'purpose': 'expected_source_rejection' if diagnostics is not None else 'native_build',
               'sources': {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sources},
               'environment': {name: env[name] for name in
                               ('GOTOOLCHAIN','GOPROXY','GOSUMDB','GOWORK','GO111MODULE',
                                'CGO_ENABLED','GOMAXPROCS','GOCACHE')},
               'returncode': None, 'classification': 'not_started'}
    if gopath is not None:
        receipt['environment']['GOPATH'] = env['GOPATH']

    def record(stdout=b'', stderr=b''):
        (directory / 'build.stdout').write_bytes(stdout)
        (directory / 'build.stderr').write_bytes(stderr)
        receipt['stdout_sha256'] = hashlib.sha256(stdout).hexdigest()
        receipt['stderr_sha256'] = hashlib.sha256(stderr).hexdigest()
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')

    def fail(kind, stdout=b'', stderr=b'', error=None):
        receipt['classification'] = kind
        if error is not None:
            receipt['error'] = str(error)
        record(stdout, stderr)
        raise ReferenceBuildFailure(kind, receipt_path)

    record()
    try:
        executable = _resolve_reference_compiler(compiler, directory, env)
        command[0] = str(executable)
        receipt['compiler_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
        record()
        result = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        fail('timeout', error.stdout or b'', error.stderr or b'', error)
    except OSError as error:
        fail('tool_failure', error=error)
    receipt['returncode'] = result.returncode
    receipt['classification'] = 'observed_unclassified'
    receipt['binary_present'] = binary.is_file()
    if binary.is_file():
        receipt['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    record(result.stdout, result.stderr)

    if diagnostics is None:
        if result.returncode != 0:
            fail('build_failed', result.stdout, result.stderr)
        if not binary.is_file():
            fail('missing_output', result.stdout, result.stderr)
        receipt['classification'] = 'native_build_succeeded'
        record(result.stdout, result.stderr)
        return binary

    patterns = (diagnostics,) if isinstance(diagnostics, (str, bytes)) else tuple(diagnostics)
    patterns = tuple(re.escape(pattern.decode()) if isinstance(pattern, bytes) else pattern
                     for pattern in patterns)
    if not patterns or not all(isinstance(pattern, str) and pattern for pattern in patterns):
        fail('missing_expected_diagnostics', result.stdout, result.stderr)
    if result.returncode != 1:
        fail('not_normal_rejection_exit', result.stdout, result.stderr)
    if result.stdout or binary.exists():
        fail('unexpected_rejection_output', result.stdout, result.stderr)
    lowered = result.stderr.lower()
    if any(marker in lowered for marker in
           (b'panic:', b'fatal error:', b'out of memory', b'cannot allocate memory',
            b'signal:', b'internal compiler error', b'unexpected fault address',
            b'runtime:', b'exec format error', b'permission denied',
            b'cannot find package', b'no required module provides package', b'is not in std')):
        fail('abnormal_reference_failure', result.stdout, result.stderr)
    messages, unexpected = _source_messages(result.stderr, sources, directory)
    receipt['source_diagnostics'] = messages
    receipt['unexpected_stderr_lines'] = unexpected
    receipt['expected_diagnostics'] = list(patterns)
    if not messages:
        fail('unattributed_rejection', result.stdout, result.stderr)
    if unexpected:
        fail('unclassified_reference_stderr', result.stdout, result.stderr)
    if not all(any(re.search(pattern, message) for message in messages) for pattern in patterns):
        fail('unexpected_source_diagnostics', result.stdout, result.stderr)
    receipt['classification'] = 'normal_source_rejection'
    record(result.stdout, result.stderr)
    return receipt


def _reference_units(units, directory):
    directory.mkdir()
    paths = []
    for filename, source in units:
        if Path(filename).name != filename:
            raise ValueError('oracle source names must be basenames')
        path = directory / filename
        path.write_bytes(source.encode('utf-8'))
        paths.append(path.resolve())
    return paths


def compile_reference(units, directory, *, compiler, cache):
    """Positive API unchanged: identical original sources -> official binary."""
    paths = _reference_units(units, directory)
    return _reference_build(paths, paths, directory, compiler=compiler, cache=cache)


def compile_reference_rejection(units, directory, *, compiler, cache, diagnostics):
    """Observe normal, source-attributed rejection; tool failure always raises."""
    paths = _reference_units(units, directory)
    return _reference_build(paths, paths, directory, compiler=compiler, cache=cache,
                            diagnostics=diagnostics)


def _reference_packages(packages, directory):
    directory.mkdir()
    gopath = directory / 'gopath'
    main_paths, all_paths = [], []
    for import_path, source_units in packages.items():
        parts = import_path.split('/')
        if not parts or any(part in ('', '.', '..') for part in parts):
            raise ValueError('oracle import paths must be relative package paths')
        folder = gopath / 'src' / import_path
        folder.mkdir(parents=True)
        for filename, source in source_units:
            if Path(filename).name != filename:
                raise ValueError('oracle source names must be basenames')
            path = folder / filename
            path.write_bytes(source.encode('utf-8'))
            all_paths.append(path.resolve())
            if import_path == 'main':
                main_paths.append(path.resolve())
    return main_paths, all_paths, gopath


def compile_reference_packages(packages, directory, *, compiler, cache):
    """Positive API unchanged for an exact supplied source-package graph."""
    paths, sources, gopath = _reference_packages(packages, directory)
    return _reference_build(paths, sources, directory, compiler=compiler, cache=cache, gopath=gopath)


def compile_reference_packages_rejection(packages, directory, *, compiler, cache, diagnostics):
    paths, sources, gopath = _reference_packages(packages, directory)
    return _reference_build(paths, sources, directory, compiler=compiler, cache=cache,
                            gopath=gopath, diagnostics=diagnostics)

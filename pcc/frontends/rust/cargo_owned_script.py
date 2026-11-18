"""An owned, bounded Linux host execution path for dependency-free build.rs units.

A Rust script is a separate host program. Registration of a unit, parsing a
main function, or finding a Cargo directive in text is not such a run. This
initial owner handles only the existing Rust frontend's closed non-FFI subset
and a normal unit-returning main. No std, external crates, generated source,
interpreter ABI, dynamic library, external linker, or independent result is
substituted. A conventional compiled process may actually return or time out.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

from .cargo_build_plan import CargoPlatform,Unit,plan_locked_library
from .cargo_package_sources import CargoSourceError,load_cargo_source_closure
from .syntax import Parser,RustError
from .configuration import OwnedRustConfiguration,TargetPlatformFacts
from .builtin_macros import CompileEnvironment


HOST = 'x86_64-unknown-linux-gnu'
MAX_OUTPUT = 64 * 1024


class ScriptStageError(CargoSourceError):
    def __init__(self,stage,cause):
        self.stage=stage
        super().__init__(f'{stage}: {cause}')


@dataclass(frozen=True)
class ScriptRun:
    package: str
    package_archive_sha256: str
    source_path: str
    source_sha256: str
    object_sha256: str
    executable_sha256: str
    returncode: int
    stdout: bytes
    stderr: bytes

    def record(self):
        return {
            'package':self.package,'archive_sha256':self.package_archive_sha256,
            'source':self.source_path,'source_sha256':self.source_sha256,
            'object_sha256':self.object_sha256,'executable_sha256':self.executable_sha256,
            'owner':'PCC Rust frontend / owned Linux x86_64 ELF / actual host process',
            'returncode':self.returncode,'stdout_bytes':len(self.stdout),'stderr_bytes':len(self.stderr),
            'stdout_sha256':_sha(self.stdout),'stderr_sha256':_sha(self.stderr),
        }


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _process(image,timeout):
    # The compiler and ELF are built in-process. This is the only child. It
    # receives neither a shell nor inherited credentials, fds, stdin or cwd.
    # Pipes are bounded while the child runs, not after collecting all bytes.
    with tempfile.TemporaryDirectory(prefix='pcc-owned-build-script-') as temporary:
        base=Path(temporary)
        binary=base/'script'
        binary.write_bytes(image);binary.chmod(0o700)
        with subprocess.Popen([str(binary)],cwd=base,env={},stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True) as p:
            deadline=time.monotonic()+timeout
            buffers=[bytearray(),bytearray()]
            try:
                with selectors.DefaultSelector() as selector:
                    for idx,stream in enumerate((p.stdout,p.stderr)):
                        selector.register(stream,selectors.EVENT_READ,idx)
                    while selector.get_map():
                        left=deadline-time.monotonic()
                        if left<=0:
                            raise ScriptStageError('execution','build-script timeout')
                        for selected,_ in selector.select(left):
                            data=os.read(selected.fd,8192)
                            if data:
                                if len(buffers[0])+len(buffers[1])+len(data)>MAX_OUTPUT:
                                    raise ScriptStageError('execution','build-script output exceeds limit')
                                buffers[selected.data].extend(data)
                            else:
                                selector.unregister(selected.fileobj)
                try:
                    p.wait(timeout=max(0,deadline-time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise ScriptStageError('execution','build-script timeout') from exc
                return p.returncode,bytes(buffers[0]),bytes(buffers[1])
            except BaseException:
                try:
                    os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
            finally:
                p.wait()


def run_locked_leaf_script(store, root_pin, package, *, original_files, namespace='target', timeout=3.0):
    """Use the entire authenticated package script. Never reconstruct its output.

    This is an explicit independent API, not an invocation of the CI-only
    bounded-source worker. The caller guards the whole compiler/child tree.
    Selection is a hermetic default-feature, explicit native-target release
    plan, not an inference about Cargo invocations or host CPython.
    """
    if type(timeout) not in (int,float) or not 0<timeout<=10:
        raise ScriptStageError('selection','invalid bounded child timeout')
    if (sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64')
            or sys.byteorder!='little' or sys.maxsize!=(1<<63)-1):
        raise ScriptStageError('selection','actual host lacks the qualified static Linux x86_64 execution ABI')
    try:
        closure=load_cargo_source_closure(store,root_pin,original_files=original_files)
    except CargoSourceError as exc:
        raise ScriptStageError('source authentication',exc) from exc
    platform_option=CargoPlatform(HOST)
    plan=plan_locked_library(closure,host=platform_option,target=platform_option)
    match=next((p for p in plan.units if p.unit==Unit(namespace,package,'compile-build-script')),None)
    if match is None:
        raise ScriptStageError('selection','no selected compile-build-script unit')
    if match.prerequisites or match.externs:
        raise ScriptStageError('prerequisites','owned compiled build dependencies have not been provided')
    if any(e.consumer==Unit(namespace,package,'run-build-script') for e in plan.metadata_routes):
        raise ScriptStageError('prerequisites','genuine direct links metadata has not been provided')
    pkg=closure.packages[package]
    prof=closure.root.manifest.get('profile',{}).get('release',{})
    # Preserve release overflow policy; the Rust frontend currently implements
    # overflow-checks=off only. Do not ignore package-specific overrides.
    build_override=prof.get('build-override',{})
    if 'package' in prof or build_override.get('overflow-checks',prof.get('overflow-checks',False)) is not False:
        raise ScriptStageError('selection','unsupported build dependency overflow or package override')
    raw=pkg.files[pkg.build_script]
    edition=pkg.manifest['package'].get('edition','2015')
    if edition not in ('2015','2018','2021','2024'):
        raise ScriptStageError('selection','unsupported Cargo package edition')
    facts=TargetPlatformFacts()
    env=CompileEnvironment.partial({'CARGO_PKG_NAME':pkg.name,'CARGO_PKG_VERSION':pkg.version})
    try:
        source=raw.decode('utf-8')
        parser=Parser(source,source_path=pkg.build_script,edition=int(edition),
                      configuration=OwnedRustConfiguration(HOST,int(edition),facts),environment=env)
        functions=parser.parse()
        # No foreign calls, explicit external names, arbitrary entry ABI, or
        # unowned module-file resolution is admitted to this standalone owner.
        if parser.modules or parser.crate_aliases:
            raise RustError('module-file/extern crate compilation needs an owned full crate provider')
        if any(f.abi!='Rust' or f.no_mangle or f.body is None for f in functions):
            raise RustError('foreign code or externally named functions are not available in this owner')
        main=[f for f in functions if f.implementation is None and f.token.text=='main']
        if (len(main)!=1 or main[0].params or main[0].result!='()' or main[0].unsafe
                or main[0].type_parameters or main[0].lifetime_parameters or main[0].const_fn):
            raise RustError('expected one safe, non-generic fn main() returning ()')
        # The library API does not bind a filename to file!-like intrinsics.
        # Do not accidentally compile a wrong logical path in this owner.
        if any(x.name in ('file','module_path') for x in parser.macro_expansions):
            raise RustError('source-path-dependent macro is not supported for a build script')
        from . import compile_rust
        compilation=compile_rust(source,module_name='build_script_build',target_triple=HOST,
                                 overflow_checks=False,edition=int(edition),cfg=facts,env=env)
        symbol=compilation.symbols['main']
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',symbol):
            raise RustError('unexpected owned Rust main symbol')
        obj=compilation.emit_object()
    except (RustError,UnicodeError) as exc:
        raise ScriptStageError('owned Rust compilation',exc) from exc
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    # Only fn main() -> (). On normal return Rust's Termination for () gives
    # success. No program status is obtained from an undefined return register.
    # This static, closed image has no C destructors/foreign initializers.
    entry=assemble_file('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n'
                        f' call {symbol}\n xor edi, edi\n mov eax, 231\n syscall\n')
    from pcc.backend.elf_x86_64 import ElfError
    try:
        image=link_static_executable([parse_relocatable(obj),entry])
    except ElfError as exc:
        raise ScriptStageError('owned ELF link',exc) from exc
    try:
        code,out,err=_process(image,timeout)
    except OSError as exc:
        raise ScriptStageError('host process',exc) from exc
    return ScriptRun(pkg.name,pkg.archive_sha256,pkg.build_script,_sha(raw),_sha(obj),_sha(image),code,out,err)

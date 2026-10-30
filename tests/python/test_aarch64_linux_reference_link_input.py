"""Real owned ELF handoff to the host-reference linker; no native process."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from pcc.backend.arm64_encode import EncodeError
from pcc.backend.elf_x86_64 import EM_AARCH64, SHN_UNDEF, parse_relocatable
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_parse import parse_self_backend_target_triple
from pcc.backend.self_backend_targets import resolve_self_backend_target
from pcc.frontends.python import pipeline_self_backend_emit as emit
from pcc.frontends.python import pipeline_self_backend_link as link

TARGET = "aarch64-unknown-linux-gnu"
IDENTITY = "self-aarch64-linux-v0"
OTHERS = [("x86_64-unknown-linux-gnu", "self-x86_64-linux-v0"),
          ("arm64-apple-darwin", "self-aarch64-darwin-v0"),
          ("x86_64-pc-windows-msvc", "self-x86_64-windows-v0")]


def _ir(target=TARGET, name="main"):
    return f'''target triple = "{target}"
@cell = global i64 42
declare i64 @read_value()
define i64 @{name}() {{
entry:
  %v = call i64 @read_value()
  %p = load i64, ptr @cell
  %r = add i64 %v, %p
  ret i64 %r
}}
'''


def _native(text):
    target = parse_self_backend_target_triple(text)
    return resolve_self_backend_target(target).identity, emit_self_asm(text)


def _noop(*args, **kwargs):
    return None


def _unexpected(*args, **kwargs):
    pytest.fail("unexpected process, worker, cache, or split operation")


def _elf(path, name="main"):
    data = Path(path).read_bytes()
    assert data[:4] == b"\x7fELF"
    obj = parse_relocatable(data)
    assert obj.machine == EM_AARCH64
    symbols = {s.name: s for s in obj.symbols}
    assert symbols[name].section_index != SHN_UNDEF
    assert symbols["cell"].section_index != SHN_UNDEF
    assert symbols["read_value"].section_index == SHN_UNDEF
    assert any(s.name == ".text" and s.data for s in obj.sections)
    assert any(s.relocations for s in obj.sections)
    return data


def _batch(texts, directory, *, internal_link=False, emitter=_native):
    return emit.emit_objects_many_in_process(
        texts, str(directory), "reference-cc", split_large_modules=False,
        profile=None, internal_link=internal_link,
        parse_target_triple=parse_self_backend_target_triple,
        host_target_triple=lambda: TARGET, target_supported=lambda _: True,
        native_worker_executable=lambda: "", split_large_ir_modules=_unexpected,
        source_workers_worthwhile=lambda _: False,
        worker_command_prefix_for_frontend=_unexpected,
        split_threshold_bytes=lambda: 1000000, split_shard_bytes=lambda: 1000000,
        jobs_for_ir_texts=_unexpected, profile_counter=_noop,
        profiled_gc_collect=_noop, profile_begin=_noop, profile_end=_noop,
        run_worker_commands=_unexpected, small_int_decimal=str,
        shell_quote_arg=_unexpected, split_worker_arg="--split-unused",
        plan_cache=_unexpected, jobs=_unexpected, jobs_for_input_sizes=_unexpected,
        jobs_env="PCC_UNUSED", run_emit_worker_pool=_unexpected,
        publish_cache=_unexpected, maintain_cache=_unexpected,
        emit_in_process=emitter, join_strings=lambda xs, sep: sep.join(xs),
    )


def _link(texts, directory, observe, *, mode="cc", emitter=_native, consume=False):
    def objects(inputs, tmp, cc, **kw):
        assert cc == "reference-cc"
        assert kw['split_large_modules'] is False
        return _batch(inputs, tmp, internal_link=kw['internal_link'], emitter=emitter)

    return link.link_ir_texts_run(
        texts, str(directory / "output"), "runtime.a", False,
        needs_libpython=False, needs_native_extension_exports=True,
        extra_link_inputs=("external.o",), extra_link_args=("-explicit-flag",),
        tmp=str(directory), profile=None, consume_ir_texts=consume,
        resolve_self_link_mode=lambda **_: mode,
        validate_pcc_self_link_surface=_noop,
        profile_begin=_noop, profile_end=_noop, debug_dump_ir_texts=_noop,
        split_large_modules_enabled=lambda: False, split_threshold_bytes=lambda: 1000000,
        emit_asm=lambda text, *_: emitter(text), emit_objects=objects,
        runtime_archive_link_args=lambda archive, exported: [archive, "-capi-export" if exported else "-no-capi"],
        native_extension_export_flags=lambda exported: ["-native-export"] if exported else [],
        libpython_isolation_flags=lambda *_: ["-isolate"],
        platform_link_flags=lambda: ["-platform"], append_libpython_link_flags=_unexpected,
        log=_noop, join_strings=lambda xs, sep: sep.join(xs),
        run_self_link_command=observe, finish_self_backend_executable=_noop,
        semantic_layout_enabled=lambda: False, write_semantic_layout_policy=_unexpected,
    )


@pytest.mark.parametrize("name", ["main", "second"])
def test_reference_worker_writes_real_elf_without_host_assembler(tmp_path, monkeypatch, name):
    monkeypatch.setattr(emit.subprocess, "run", _unexpected)
    source = tmp_path / "input.ll"; source.write_text(_ir(name=name))
    output = tmp_path / "output.o"; result = tmp_path / "result"
    calls = []
    def native(text):
        calls.append(text)
        return _native(text)
    assert emit.run_emit_worker(str(source), str(result), str(output), "reference-cc",
                                normalize_ir=lambda x: x, emit_native=native) == 0
    assert calls == [source.read_text()]
    assert result.read_text().splitlines() == [IDENTITY, str(output)]
    _elf(output, name)


@pytest.mark.parametrize("target,identity", OTHERS)
def test_other_worker_targets_retain_exact_external_assembler(tmp_path, monkeypatch, target, identity):
    source = tmp_path / "input.ll"; source.write_text(_ir(target))
    output = tmp_path / "output.o"; result = tmp_path / "result"; calls = []
    monkeypatch.setattr(emit.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    assert emit.run_emit_worker(str(source), str(result), str(output), "reference-cc",
                                normalize_ir=lambda x: x,
                                emit_native=lambda _: (identity, "original transport\n")) == 0
    assert calls == [(["reference-cc", "-c", str(result) + ".s", "-o", str(output)], {"check": True})]
    assert Path(str(result) + ".s").read_text() == "original transport\n"


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("internal", [False, True])
def test_in_process_batch_preserves_module_boundaries_and_owner(tmp_path, monkeypatch, count, internal):
    monkeypatch.setattr(emit.subprocess, "run", _unexpected)
    texts = [_ir(name="unit" + str(i)) for i in range(count)]; calls = []
    def native(text):
        calls.append(text); return _native(text)
    pairs = _batch(texts, tmp_path, internal_link=internal, emitter=native)
    assert calls == texts and len(pairs) == count
    for i, (target, path) in enumerate(pairs):
        assert target == IDENTITY
        if internal:
            assert path.endswith(".s") and Path(path).read_text() == _native(texts[i])[1]
        else:
            assert path.endswith(".o"); _elf(path, "unit" + str(i))


@pytest.mark.parametrize("count", [1, 2])
def test_reference_link_receives_real_elf_and_unchanged_exports(tmp_path, monkeypatch, count):
    monkeypatch.setenv("CC", "reference-cc"); monkeypatch.setattr(emit.subprocess, "run", _unexpected)
    texts = [_ir(name="unit" + str(i)) for i in range(count)]; original = list(texts); calls = []; emitted = []
    def native(text):
        emitted.append(text); return _native(text)
    def observe(cmd, asm, output, runtime, extras, verbose, **kw):
        calls.append(cmd)
        inputs = [x for x in cmd if x.startswith(str(tmp_path)) and x.endswith(".o")]
        assert len(inputs) == count
        for i, path in enumerate(inputs): _elf(path, "unit" + str(i))
        assert cmd[0] == "reference-cc" and cmd.count("runtime.a") == 1
        for flag in ("external.o", "-capi-export", "-explicit-flag", "-native-export", "-isolate", "-platform"):
            assert cmd.count(flag) == 1
        assert runtime == "runtime.a" and extras == ("external.o",)
        assert kw['needs_libpython'] is False and kw['needs_native_extension_exports'] is True
        assert not kw.get('pcc_asm_inputs') and not kw.get('pcc_native_object_inputs')
    _link(texts, tmp_path, observe, emitter=native, consume=True)
    assert len(calls) == 1 and emitted == original and texts == []


@pytest.mark.parametrize("target,identity", OTHERS)
def test_other_single_reference_targets_keep_assembly(tmp_path, monkeypatch, target, identity):
    monkeypatch.setenv("CC", "reference-cc"); calls = []
    def observe(cmd, asm, *args, **kwargs):
        calls.append(cmd); assert cmd[1] == asm == str(tmp_path / "self_backend.s")
        assert Path(asm).read_text().startswith("original transport")
    _link([_ir(target)], tmp_path, observe, emitter=lambda _: (identity, "original transport\n"))
    assert len(calls) == 1 and not (tmp_path / "self_backend.o").exists()


@pytest.mark.parametrize("target", [TARGET, "arm64-apple-darwin"])
def test_owned_link_never_calls_reference_conversion(tmp_path, monkeypatch, target):
    monkeypatch.setenv("CC", "reference-cc"); monkeypatch.setattr(emit, "write_aarch64_linux_reference_object", _unexpected)
    monkeypatch.setattr(emit.subprocess, "run", _unexpected); calls = []
    _link([_ir(target)], tmp_path, lambda *a, **kw: calls.append((a, kw)), mode="pcc")
    assert len(calls) == 1


def test_mixed_targets_rejected_before_emission_or_link(tmp_path, monkeypatch):
    monkeypatch.setenv("CC", "reference-cc")
    with pytest.raises(link.SelfBackendLinkError, match="^cannot link modules with different targets$"):
        _link([_ir(), _ir("x86_64-unknown-linux-gnu")], tmp_path, _unexpected, emitter=_unexpected)
    assert list(tmp_path.iterdir()) == []


def test_malformed_internal_assembly_is_not_external_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(emit.subprocess, "run", _unexpected)
    output = tmp_path / "bad.o"
    with pytest.raises(EncodeError, match="^mnemonic 'invalid_opcode' not in the proven subset:"):
        emit.write_aarch64_linux_reference_object(IDENTITY, ".section __TEXT,__text,regular,pure_instructions\ninvalid_opcode x0\n", str(output))
    assert not output.exists()


def test_reference_link_failure_keeps_error_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("CC", "reference-cc")
    failures = []
    def fail(cmd, *args, **kwargs):
        _elf(cmd[1]); error = subprocess.CalledProcessError(7, cmd, stderr="reference linker sentinel")
        failures.append(error); raise error
    with pytest.raises(link.SelfBackendLinkError) as caught:
        _link([_ir()], tmp_path, fail)
    assert len(failures) == 1
    assert str(caught.value) == "self backend link failed: CalledProcessError; exit=7; " + str(failures[0])


@pytest.mark.parametrize("target,identity", OTHERS)
def test_other_in_process_targets_keep_exact_external_assembly(tmp_path, monkeypatch, target, identity):
    calls = []
    monkeypatch.setattr(emit.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    pairs = _batch([_ir(target)], tmp_path, emitter=lambda _: (identity, "original transport\n"))
    asm = str(tmp_path / "self_backend_native_0.s"); obj = str(tmp_path / "self_backend_native_0.o")
    assert pairs == [(identity, obj)]
    assert calls == [(["reference-cc", "-c", asm, "-o", obj], {"check": True})]
    assert Path(asm).read_text() == "original transport\n"


@pytest.mark.parametrize("entry", ["worker", "batch"])
def test_native_runtime_cannot_select_host_reference_assembly(tmp_path, monkeypatch, capsys, entry):
    # Exercise only the Python guard, not a compiled native runtime or process.
    monkeypatch.setattr(emit.sys, "implementation", SimpleNamespace(**{**vars(emit.sys.implementation), "name": "pcc"}))
    monkeypatch.setattr(emit.subprocess, "run", _unexpected)
    monkeypatch.setattr(emit, "write_aarch64_linux_reference_object", _unexpected)
    if entry == "worker":
        assert emit.run_emit_worker(str(tmp_path / "absent.ll"), str(tmp_path / "result"),
                                    str(tmp_path / "output.o"), "reference-cc",
                                    normalize_ir=_unexpected, emit_native=_unexpected) == 1
        assert capsys.readouterr().err == "self backend emit worker failed: native self emission cannot invoke an external assembler\n"
    else:
        with pytest.raises(emit.SelfBackendEmitError, match="^native self batch emission requires owned object emission$"):
            _batch([_ir()], tmp_path, emitter=_unexpected)
    assert list(tmp_path.iterdir()) == []

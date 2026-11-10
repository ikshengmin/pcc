"""IR owner retirement at the committed self-backend worker boundary."""
from __future__ import annotations

import shlex
import subprocess
import sys
import weakref
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_self_backend_emit as emit
from pcc.backend.self_backend_parse import parse_self_backend_target_triple


TARGET = "x86_64-unknown-linux-gnu"
HEADER = 'target triple = "' + TARGET + '"\n'
TARGET_ID = "self-x86_64-linux-v0"


class _Text(str):
    """Weak-referenceable payload, with no owner hidden in test snapshots."""


def _options(tmp_path, texts, *, consume=False, split_copy=False, hits=(),
             state=None, fail_at=None, in_process=False):
    if state is None:
        state = {}
    state.update(admission=[], pools=[], released=0, split_owners=[], counters={})
    outer = list(texts)
    expected = [str(text) + ("; shard\n" if split_copy else "") for text in texts]
    state["outer"] = outer
    state["references"] = [weakref.ref(text) for text in texts]

    def release():
        state["released"] += 1
        outer.clear()

    def split(values):
        result = [_Text(str(text) + "; shard\n") for text in values] if split_copy else values
        state["split_owners"].append(result)
        state["references"].extend(weakref.ref(text) for text in result)
        return result

    def admission(values, **_kwargs):
        assert values and outer, "admission saw consumed input"
        state["admission"].append(tuple(len(text) for text in values))
        if fail_at == "admission":
            raise RuntimeError("admission failed")
        return 2

    def result(result_path, object_path, status):
        Path(object_path).write_bytes(b"mock packed object")
        Path(result_path).write_text(TARGET_ID + "\n" + object_path + "\n" + status + "\n")

    def cache(items, *_args):
        if fail_at == "cache":
            raise RuntimeError("cache failed")
        plan = []
        for index, (result_path, object_path, _ir_path) in enumerate(items):
            status = "hit" if index in hits else "off"
            if status == "hit":
                result(result_path, object_path, status)
            plan.append(("", status))
        return plan

    def pool(_prefix, items, _cc, _directory, label, jobs, fresh, **_kwargs):
        state["pools"].append((label, jobs, fresh))
        assert bool(texts) is not consume
        assert bool(outer) is not consume
        assert all((reference() is None) is consume for reference in state["references"])
        if consume:
            owner_frame = _kwargs.get("_owner_frame")
            if owner_frame is None:
                owner_frame = sys._getframe(1)
            assert owner_frame.f_code.co_name == "emit_objects_many_in_process"
            assert owner_frame.f_locals["ir_text"] == ""
        if fail_at == "worker":
            raise RuntimeError("worker failed")
        for result_path, object_path, ir_path in items:
            index = int(Path(ir_path).stem.rsplit("_", 1)[1])
            assert Path(ir_path).read_text() == expected[index]
            result(result_path, object_path, "off")
        return 1

    return dict(
        split_large_modules=True, profile=None, internal_link=True,
        consume_ir_texts=consume, release_ir_texts=release,
        parse_target_triple=parse_self_backend_target_triple,
        host_target_triple=lambda: TARGET, target_supported=lambda _target: True,
        native_worker_executable=lambda: "",
        split_large_ir_modules=split,
        source_workers_worthwhile=lambda _values: not in_process,
        worker_command_prefix_for_frontend=lambda: ["mock-source-worker"],
        split_threshold_bytes=lambda: 2_000_000, split_shard_bytes=lambda: 1_000_000,
        jobs_for_ir_texts=admission,
        profile_counter=lambda _profile, name, value: state["counters"].__setitem__(name, value),
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        profile_begin=lambda _profile: 0, profile_end=lambda *_args: None,
        run_worker_commands=lambda *_args, **_kwargs: pytest.fail("unexpected split worker"),
        small_int_decimal=str, shell_quote_arg=shlex.quote, split_worker_arg="--split",
        plan_cache=cache, jobs=lambda count: min(2, count),
        jobs_for_input_sizes=lambda sizes, **_kwargs: min(2, len(sizes)),
        jobs_env="PCC_SELF_BACKEND_JOBS", run_emit_worker_pool=pool,
        publish_cache=lambda *_args: True, maintain_cache=lambda *_args: None,
        emit_in_process=lambda _text: None, join_strings=lambda values, sep: sep.join(values),
    )


@pytest.fixture
def forbid_processes(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("coordinator-only regression attempted a real process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(emit.gc, "collect", forbidden)


@pytest.mark.parametrize("consume", [False, True])
@pytest.mark.parametrize("split_copy", [False, True])
@pytest.mark.parametrize("hits", [(), (0,), (0, 1)])
def test_worker_boundary_releases_only_transferred_ir(
    tmp_path, forbid_processes, consume, split_copy, hits,
):
    texts = [_Text(HEADER + "; first\n"), _Text(HEADER + "; last\n")]
    state = {}
    options = _options(tmp_path, texts, consume=consume, split_copy=split_copy,
                       hits=hits, state=state)
    results = emit.emit_objects_many_in_process(texts, str(tmp_path), "", **options)
    assert [Path(path).name for _target, path in results] == [
        "self_backend_native_0.pco", "self_backend_native_1.pco",
    ]
    assert state["released"] == int(consume)
    assert len(state["admission"]) == (0 if hits == (0, 1) else 1)
    assert bool(state["pools"]) is (hits != (0, 1))
    assert bool(texts) is not consume
    assert all(bool(values) is not consume for values in state["split_owners"])
    assert all((reference() is None) is consume for reference in state["references"])


@pytest.mark.parametrize("failure", ["cache", "admission", "worker"])
@pytest.mark.parametrize("consume", [False, True])
def test_errors_preserve_inputs_until_worker_commit(tmp_path, forbid_processes, failure, consume):
    texts = [_Text(HEADER + "; first\n"), _Text(HEADER + "; last\n")]
    state = {}
    options = _options(tmp_path, texts, consume=consume, state=state, fail_at=failure)
    with pytest.raises(RuntimeError, match=failure + " failed"):
        emit.emit_objects_many_in_process(texts, str(tmp_path), "", **options)
    committed = consume and failure == "worker"
    assert bool(texts) is not committed
    assert bool(state["outer"]) is not committed
    assert state["released"] == int(committed)


@pytest.mark.parametrize("route", ["unsupported", "mixed", "in_process"])
def test_nonworker_routes_preserve_transferred_inputs(tmp_path, forbid_processes, route):
    texts = [_Text(HEADER + "; first\n"), _Text(HEADER + "; last\n")]
    if route == "unsupported":
        texts[0] = _Text('target triple = "riscv64-unknown-none"\n')
    elif route == "mixed":
        texts[1] = _Text('target triple = "arm64-apple-darwin23.6.0"\n')
    state = {}
    options = _options(tmp_path, texts, consume=True, state=state, in_process=route == "in_process")
    if route == "mixed":
        with pytest.raises(emit.SelfBackendEmitError, match="different target platforms"):
            emit.emit_objects_many_in_process(texts, str(tmp_path), "", **options)
    else:
        assert emit.emit_objects_many_in_process(texts, str(tmp_path), "", **options) is None
    assert len(texts) == len(state["outer"]) == 2
    assert state["released"] == 0
    assert state["pools"] == state["admission"] == []


def test_host_fallback_receives_complete_inputs_and_keeps_default_callback_signature(tmp_path, monkeypatch):
    texts = [HEADER + "; first\n", HEADER + "; last\n"]
    calls = []

    def fallback(values, _directory, _cc, *, split_large_modules, profile, internal_link):
        assert values == texts
        calls.append("native")
        return None

    def subprocess_run(command, **_kwargs):
        assert [Path(path).read_text() for path in command[9:]] == texts
        Path(command[7]).write_text("0\t" + TARGET_ID + "\tfirst.pco\n1\t" + TARGET_ID + "\tlast.pco\n")
        calls.append("fallback")

    monkeypatch.setattr(emit.subprocess, "run", subprocess_run)
    results = emit.emit_objects_many_via_host_python(
        texts, str(tmp_path), "", internal_link=True,
        emit_in_process_many=fallback, profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None, split_threshold_bytes=lambda: 2_000_000,
        jobs_for_count=lambda _count: 1, host_python_command=lambda: "python",
        host_many_code="mock", pcc_source_root=lambda: "source", small_int_decimal=str,
        profile_counter=lambda *_args: None,
    )
    assert calls == ["native", "fallback"]
    assert results == [(TARGET_ID, "first.pco"), (TARGET_ID, "last.pco")]
    assert len(texts) == 2


def test_link_callback_drops_outer_list_and_dead_scalar_aliases(tmp_path, monkeypatch):
    texts = [_Text(HEADER + "; first\n"), _Text(HEADER + "; last\n")]
    references = [weakref.ref(value) for value in texts]

    def emit_objects(values, *_args, consume_ir_texts=False, release_ir_texts=None, **_kwargs):
        assert consume_ir_texts and len(texts) == len(values) == 2
        release_ir_texts()
        assert texts == []
        assert all(reference() is None for reference in references)
        return [(TARGET_ID, str(tmp_path / "module.pco"))]

    monkeypatch.setattr(pipeline, "_emit_self_objects_many_via_host_python", emit_objects)
    monkeypatch.setattr(pipeline, "_run_self_link_command", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_finish_self_backend_executable", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_macho_semantic_layout_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_self_backend_split_large_modules_enabled", lambda: False)
    pipeline._link_with_self_backend_ir_texts(
        texts, str(tmp_path / "program"), None, False,
        tmp_dir=str(tmp_path), consume_ir_texts=True,
    )


@pytest.mark.parametrize("consume", [False, True])
def test_link_error_before_commit_preserves_original_list(tmp_path, monkeypatch, consume):
    texts = [HEADER + "; first\n", HEADER + "; last\n"]
    expected = list(texts)

    def fail(*_args, **_kwargs):
        raise RuntimeError("before worker commitment")

    monkeypatch.setattr(pipeline, "_emit_self_objects_many_via_host_python", fail)
    monkeypatch.setattr(pipeline, "_macho_semantic_layout_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_self_backend_split_large_modules_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="before worker commitment"):
        pipeline._link_with_self_backend_ir_texts(
            texts, str(tmp_path / "program"), None, False,
            tmp_dir=str(tmp_path), consume_ir_texts=consume,
        )
    assert texts == expected


@pytest.mark.integration
@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux"
    and __import__("platform").machine().lower() in ("x86_64", "amd64"),
)
def test_consumed_and_borrowed_worker_inputs_emit_identical_executed_elf(tmp_path, monkeypatch):
    """Real host self emission, owned link and native execution; no runtime build."""
    from pcc.backend import owned_elf_link

    programs = [HEADER + '''declare i32 @answer()
define i32 @main() {
entry:
  %value = call i32 @answer()
  ret i32 %value
}
''', HEADER + '''define i32 @answer() {
entry:
  ret i32 42
}
''']
    startup = '''.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
  call main
  mov edi, eax
  mov eax, 60
  syscall
.size _start, .-_start
.section .note.GNU-stack,"",@progbits
'''
    outputs = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("owned emitter or linker attempted an external process")

    for consume in (False, True):
        directory = tmp_path / ("consumed" if consume else "borrowed")
        directory.mkdir()
        texts = [_Text(text) for text in programs]
        state = {}
        options = _options(directory, texts, consume=consume, state=state)
        checked_pool = options["run_emit_worker_pool"]

        def real_pool(prefix, items, cc, tmp, label, jobs, fresh, **kwargs):
            # Check the owner boundary, then overwrite mock artifacts with the
            # real worker's packed objects from exactly those persisted inputs.
            checked_pool(
                prefix, items, cc, tmp, label, jobs, fresh,
                _owner_frame=sys._getframe(1), **kwargs,
            )
            for result_path, object_path, ir_path in items:
                assert pipeline.run_self_backend_emit_worker(
                    ir_path, result_path, object_path, "",
                ) == 0
            return 1

        options["run_emit_worker_pool"] = real_pool
        with monkeypatch.context() as guard:
            guard.setattr(subprocess, "Popen", forbidden)
            results = emit.emit_objects_many_in_process(texts, str(directory), "", **options)
            start = directory / "startup.s"
            start.write_text(startup)
            output = directory / "program"
            owned_elf_link.link_inputs(
                target=TARGET, output=str(output), assembly=[str(start)],
                objects=[path for _target, path in results],
            )
        result = subprocess.run([str(output)], capture_output=True, timeout=10)
        assert result.returncode == 42, result.stderr
        assert result.stdout == result.stderr == b""
        assert bool(texts) is not consume
        assert state["released"] == int(consume)
        outputs.append(([Path(path).read_bytes() for _target, path in results], output.read_bytes()))
    assert outputs[0] == outputs[1]

"""Bounded worker error transport, independent of the failure's cause."""

from pathlib import Path
import ast
import io
import os
import subprocess
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_frontend_parallel as parallel
from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy


# CPython's Unicode whitespace set, including the controls the wire rejects.
_UNICODE_WHITESPACE = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20,
    0x85, 0xA0, 0x1680, *range(0x2000, 0x200B),
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
)
_MESSAGE_SPACES = (
    0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000,
)


def test_worker_error_whitespace_inventory_matches_cpython():
    assert tuple(codepoint for codepoint in range(0x110000) if chr(codepoint).isspace()) == _UNICODE_WHITESPACE
    assert _MESSAGE_SPACES == tuple(
        codepoint for codepoint in _UNICODE_WHITESPACE
        if codepoint >= 32 and codepoint not in (0x85, 0x2028, 0x2029)
    )


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("codepoint", _UNICODE_WHITESPACE, ids=lambda value: f"U+{value:04X}")
def test_worker_error_reader_rejects_each_unicode_whitespace_only_message(
    tmp_path, terminator, codepoint,
):
    result = tmp_path / "worker.tsv"
    result.write_bytes(("ERR\t" + chr(codepoint) * 3 + terminator).encode("utf-8"))
    assert workers.read_worker_error(str(result)) == ""


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("space", [
    *(chr(codepoint) for codepoint in _MESSAGE_SPACES),
    "".join(chr(codepoint) for codepoint in _MESSAGE_SPACES),
], ids=[*(f"U+{value:04X}" for value in _MESSAGE_SPACES), "mixed"])
def test_worker_error_reader_preserves_unicode_spaces_around_content(tmp_path, terminator, space):
    result = tmp_path / "worker.tsv"
    result.write_bytes(("ERR\t" + space + terminator).encode("utf-8"))
    assert workers.read_worker_error(str(result)) == ""
    message = space + "ValueError: 模块 failed" + space
    result.write_bytes(("ERR\t" + message + terminator).encode("utf-8"))
    assert workers.read_worker_error(str(result)) == message


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("codepoint", [0x180E, 0x200B, 0x2060, 0xFEFF])
def test_worker_error_reader_does_not_expand_unicode_whitespace_set(tmp_path, terminator, codepoint):
    result = tmp_path / "worker.tsv"
    message = chr(codepoint)
    assert not message.isspace()
    result.write_bytes(("ERR\t" + message + terminator).encode("utf-8"))
    assert workers.read_worker_error(str(result)) == message


def test_worker_failure_regressions_are_in_bounded_shared_ci():
    from scripts.ci_validation_run import modules

    assert "tests/python/test_frontend_worker_failure_diagnostics.py" in modules("shared")


@pytest.mark.parametrize("payload", [
    None, b"", b"ERR", b"ERR\t", b"ERR\t\n", b"ERR\t  \n",
    b"ERR\tpartial", b"ERR\tfirst\nERR\tsecond\n",
    b"ERR\tfirst\tsecond\n", b"ERR\tfirst\rsecond\n",
    b"ERR\tfirst\x00second\n", b"ERR\t\x1b[31mred\n",
    b"ERR\tfirst\x7fsecond\n", b"ERR\t\xff\n",
    b"OK\t0\tentry\t0\t0\t0\tmodule.ll\n",
    b"OK\t0\tentry\nERR\tfailure\n",
    b"ERR\tfailure\nOK\t0\tentry\n",
    b"ERR\t" + b"x" * workers.WORKER_ERROR_MAX_BYTES + b"\n",
    "ERR\tfirst\u2028second\n".encode("utf-8"),
    b"ERR\t\r\n", b"ERR\t  \r\n", b"ERR\tpartial\r",
    b"ERR\tfirst\rsecond\r\n", b"ERR\tfirst\r\r\n",
    b"ERR\tfirst\n\r", b"ERR\tfirst\r\nERR\tsecond\r\n",
    b"ERR\tfirst\r\n\r\n", b"ERR\tfirst\x00second\r\n",
    b"ERR\t\xff\r\n", "ERR\tfirst\u2028second\r\n".encode("utf-8"),
    b"OK\t0\tentry\r\n", b"ERR\tfailure\r\nOK\t0\tentry\r\n",
])
def test_worker_error_reader_ignores_missing_or_invalid_result(tmp_path, payload):
    result = tmp_path / "worker.tsv"
    if payload is not None:
        result.write_bytes(payload)
    assert workers.read_worker_error(str(result)) == ""


@pytest.mark.parametrize("terminator", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_worker_error_reader_preserves_complete_bounded_diagnostic(tmp_path, terminator):
    result = tmp_path / "worker.tsv"
    message = "  ValueError: unsupported operation in café.py / 模块  "
    result.write_bytes(b"ERR\t" + message.encode("utf-8") + terminator)
    # Leading/trailing message spaces are preserved, not globally stripped.
    assert workers.read_worker_error(str(result)) == message


@pytest.mark.parametrize("terminator", [b"\n", b"\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("unit", ["x", "é", "模"], ids=["ascii", "utf8-two", "utf8-three"])
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["below", "at", "above"])
def test_worker_error_reader_bounds_actual_wire_bytes(tmp_path, terminator, unit, offset):
    result = tmp_path / "worker.tsv"
    size = workers.WORKER_ERROR_MAX_BYTES + offset
    message_bytes = size - len(b"ERR\t") - len(terminator)
    width = len(unit.encode("utf-8"))
    message = unit * (message_bytes // width) + "x" * (message_bytes % width)
    payload = b"ERR\t" + message.encode("utf-8") + terminator
    assert len(payload) == size
    result.write_bytes(payload)
    assert workers.read_worker_error(str(result)) == (message if offset <= 0 else "")


def test_worker_error_crlf_extra_byte_does_not_expand_wire_limit(tmp_path):
    result = tmp_path / "worker.tsv"
    message = "x" * (workers.WORKER_ERROR_MAX_BYTES - len(b"ERR\t\n"))
    result.write_bytes(b"ERR\t" + message.encode("ascii") + b"\n")
    assert result.stat().st_size == workers.WORKER_ERROR_MAX_BYTES
    assert workers.read_worker_error(str(result)) == message
    result.write_bytes(b"ERR\t" + message.encode("ascii") + b"\r\n")
    assert result.stat().st_size == workers.WORKER_ERROR_MAX_BYTES + 1
    assert workers.read_worker_error(str(result)) == ""


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"], ids=["nel", "line", "paragraph"])
def test_worker_error_reader_explicitly_rejects_unicode_line_boundaries(
    tmp_path, terminator, separator,
):
    result = tmp_path / "worker.tsv"
    # Embedded non-ASCII boundaries cannot be rejected merely by the blank
    # message check, and production does not call host splitlines below.
    result.write_bytes(("ERR\tfirst" + separator + "second" + terminator).encode("utf-8"))
    assert workers.read_worker_error(str(result)) == ""


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_worker_error_reader_retains_all_ascii_control_rejections(tmp_path, terminator):
    result = tmp_path / "worker.tsv"
    for codepoint in (*range(32), 127):
        payload = "ERR\tfirst" + chr(codepoint) + "second" + terminator
        result.write_bytes(payload.encode("utf-8"))
        assert workers.read_worker_error(str(result)) == "", hex(codepoint)


def test_worker_error_reader_does_not_depend_on_host_whitespace_methods():
    module = ast.parse(Path(workers.__file__).read_text(encoding="utf-8"))
    reader = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "read_worker_error")
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"splitlines", "strip", "lstrip", "rstrip", "isspace"}
        for node in ast.walk(reader)
    )
    # Pair the behavior cases with a source-level guard on their production
    # owner: the three non-ASCII boundaries are explicit numeric comparisons.
    compared = {
        node.comparators[0].value
        for node in ast.walk(reader)
        if isinstance(node, ast.Compare) and len(node.comparators) == 1
        and isinstance(node.left, ast.Name) and node.left.id == "codepoint"
        and isinstance(node.comparators[0], ast.Constant)
    }
    assert {0x85, 0x2028, 0x2029} <= compared


def test_worker_error_reader_cannot_mask_failure_with_read_error(tmp_path, monkeypatch):
    def fail_open(*_args, **_kwargs):
        raise PermissionError("result unavailable")

    monkeypatch.setattr("builtins.open", fail_open)
    assert workers.read_worker_error(str(tmp_path / "worker.tsv")) == ""


def test_worker_error_writer_keeps_one_record_for_all_line_endings(tmp_path):
    result = tmp_path / "worker.tsv"
    workers.write_worker_error(str(result), "first\tsecond\nthird\rfourth café 模块")
    message = "first second third fourth café 模块"
    assert result.read_bytes() == b"ERR\t" + message.encode("utf-8") + os.linesep.encode("ascii")
    assert workers.read_worker_error(str(result)) == message


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_worker_error_writer_roundtrips_real_text_translation(tmp_path, monkeypatch, newline):
    result = tmp_path / "worker.tsv"
    real_open = open

    def translated_open(path, mode, **kwargs):
        if path == str(result) and mode == "w":
            assert kwargs == {"encoding": "utf-8"}
            # Exercise the actual writer with CPython's real text translator
            # on every host. This does not claim Windows/native execution.
            return io.TextIOWrapper(real_open(path, "wb"), encoding="utf-8", newline=newline)
        return real_open(path, mode, **kwargs)

    monkeypatch.setattr("builtins.open", translated_open)
    workers.write_worker_error(str(result), "ValueError: first\rsecond\nthird")
    message = "ValueError: first second third"
    assert result.read_bytes() == b"ERR\t" + message.encode("utf-8") + newline.encode("ascii")
    assert workers.read_worker_error(str(result)) == message


@pytest.fixture
def coordinator_case(tmp_path, monkeypatch):
    for name in (
        "PCC_PY_FRONTEND_IN_PROCESS_CODEGEN", "PCC_HOST_INDEXED_PROCESS_SPLIT",
        "PCC_HOST_CODEGEN_BATCH", "PCC_STAGE1_CHECKPOINT_DIR",
        "PCC_STAGE1_CHECKPOINT_BUILD", "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
        "PCC_INDEXED_HANDOFF_REQUEST", "PCC_WORKER_TREE_BUDGET_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    sources = [tmp_path / "first.py", tmp_path / "second.py"]
    for source in sources:
        source.write_text("x = 1\n", encoding="utf-8")
    case = SimpleNamespace(
        payloads=["success", "ERR\tValueError: unsupported ordinary operation\n"],
        manifests={}, calls=[], error=None, failed_index=1,
        unknown_command=False, succeed=False, prefix=["pcc1"],
        error_type=subprocess.CalledProcessError, missing_command=False,
    )

    def write_manifest(path, result, ir_dir, _exports, _ast, _sources,
                       _names, indices, **_kwargs):
        case.manifests[path] = (result, ir_dir, indices[0])
        Path(path).write_text("fixture manifest " + str(indices[0]), encoding="utf-8")

    def publish(manifest):
        result, ir_dir, index = case.manifests[manifest]
        payload = case.payloads[index]
        if payload == "success":
            ir = Path(ir_dir) / ("module_" + str(index) + ".ll")
            ir.write_text("; module " + str(index), encoding="utf-8")
            payload = "\t".join(("OK", str(index), sources[index].stem,
                                  "0", "0", "10", str(ir))) + "\n"
        if payload is not None:
            Path(result).write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)

    def make_error(command):
        case.error = case.error_type(7, command, stderr="untrusted stderr")
        if case.missing_command:
            del case.error.cmd
        return case.error

    def run_commands(commands, **_kwargs):
        case.calls.extend(commands)
        for manifest in case.manifests:
            publish(manifest)
        if not case.succeed:
            command = "unowned command" if case.unknown_command else commands[case.failed_index]
            raise make_error(command)

    def run_in_process(manifest):
        case.calls.append(manifest)
        publish(manifest)
        index = case.manifests[manifest][2]
        return 7 if index == case.failed_index and not case.succeed else 0

    def run():
        return parallel.compile_parallel_uncached(
            [str(source) for source in sources], [source.stem for source in sources],
            jobs=2, entry_module="first", sibling_inits=("second",),
            ir_scaffold_mode="on", verbose=False, artifact_dir=str(tmp_path / "artifacts"),
            can_spawn_worker=lambda: True, worker_command_prefix=lambda: case.prefix,
            chunk_count_for_workers=lambda *_args: 2,
            codegen_chunks=lambda *_args: [[0], [1]], ast_wire_enabled=lambda: False,
            build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
            write_manifest=write_manifest, shell_quote_arg=str, worker_arg="--worker",
            worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
            join_strings=lambda values, separator: separator.join(values),
            run_worker_commands=run_commands, run_worker_manifest_in_process=run_in_process,
            profiled_gc_collect=lambda *_args, **_kwargs: None,
            read_worker_ir=lambda path, _name: Path(path).read_text(encoding="utf-8"),
            profile_begin=lambda _profile: 0, profile_end=lambda *_args: None,
            profile_counter=lambda *_args: None, pipeline_error=RuntimeError,
        )

    case.run = run
    case.publish = publish
    case.make_error = make_error
    return case


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("peer_payload", ["success", None, "ERR\twrong peer failure\n"])
def test_parallel_failure_uses_exact_failed_command_result(coordinator_case, peer_payload, terminator):
    case = coordinator_case
    case.payloads[0] = peer_payload
    case.payloads[1] = "ERR\tValueError: unsupported ordinary operation" + terminator
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == (
        "parallel frontend codegen worker failed: ValueError: unsupported ordinary operation"
    )
    assert caught.value.__cause__ is case.error
    assert "CPython" not in str(caught.value)


@pytest.mark.parametrize("payload", [None, b"ERR\t\xff\n", "success", "ERR\tpartial", "ERR\tbad\rcontrol\r\n", "ERR\tfirst\r\nERR\tsecond\r\n"])
def test_parallel_failure_keeps_generic_error_for_unusable_result(coordinator_case, payload):
    case = coordinator_case
    case.payloads = ["ERR\twrong peer failure\n", payload]
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == "parallel frontend codegen worker failed"
    assert caught.value.__cause__ is case.error


def test_parallel_failure_does_not_guess_result_for_unknown_command(coordinator_case):
    case = coordinator_case
    case.unknown_command = True
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == "parallel frontend codegen worker failed"
    assert caught.value.__cause__ is case.error


class _UnreadableCommandError(subprocess.CalledProcessError):
    def __getattribute__(self, name):
        if name == "cmd":
            raise RuntimeError("command transport unavailable")
        return super().__getattribute__(name)


@pytest.mark.parametrize("unreadable", [False, True])
@pytest.mark.parametrize("host_split", [False, True])
def test_parallel_failure_keeps_cause_when_command_is_unavailable(
    coordinator_case, monkeypatch, unreadable, host_split,
):
    case = coordinator_case
    if host_split:
        _configure_host_indexed_split(case, monkeypatch, "frontend")
    case.error_type = _UnreadableCommandError if unreadable else subprocess.CalledProcessError
    case.missing_command = not unreadable
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == "parallel frontend codegen worker failed"
    assert caught.value.__cause__ is case.error


def _configure_host_indexed_split(case, monkeypatch, failure_kind):
    from pcc.frontends.python import pipeline_frontend_worker_execution as execution
    from pcc.frontends.python import pipeline_indexed_handoff as handoff
    from pcc.frontends.python import pipeline_targets as targets
    from pcc.frontends.python import worker_process_pool as pool
    from pcc.frontends.python import worker_resource_plan as policy

    case.prefix = ["host-python", "-m", "pcc"]
    for name in (
        "PCC_HOST_INDEXED_PROCESS_SPLIT", "PCC_DIRECT_INDEXED_KERNEL_CAPTURE",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT", "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK",
    ):
        monkeypatch.setenv(name, "1")
    for name in (
        "PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "PCC_TEXT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_SIDECAR", "PCC_PY_FRONTEND_IN_PROCESS_CODEGEN",
    ):
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(512 * 1024**2))
    original_commands = []
    requests = {}

    def resources(commands):
        original_commands.extend(commands)
        return [dict(
            source_identity="fixture-" + str(index), inputs=[1] * 6,
            estimate_bytes=0, **{"class": "host-source-owner"},
        ) for index in range(len(commands))]

    monkeypatch.setattr(workers, "resource_tasks_for_commands", resources)
    monkeypatch.setattr(policy, "resource_task_order", lambda _tasks: ([1, 0], []))
    monkeypatch.setattr(execution, "_direct_owned_pass_names", lambda _name: [])
    monkeypatch.setattr(targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(handoff, "write_request", lambda path, fields: requests.update({path: fields}))

    def run_pool(commands, _tasks, _width, _budget, **_kwargs):
        case.calls.extend(commands)
        assert commands[0].startswith(handoff.ENV_REQUEST + "=")
        assert commands[0].endswith(" " + original_commands[1])
        assert "--pcc-self-backend-indexed-emit-worker" in commands[1]
        for manifest in case.manifests:
            case.publish(manifest)
        if case.succeed:
            for request in requests.values():
                Path(request["output_path"]).write_bytes(b"fixture object")
                result = Path(case.manifests[request["manifest_path"]][0])
                text = result.read_text(encoding="utf-8").rstrip("\n")
                result.write_text(text + "\tPIDX\t" + request["sidecar_path"] + "\n", encoding="utf-8")
            return
        command = commands[0]
        if failure_kind == "backend":
            command = commands[1]
        elif failure_kind == "unprefixed":
            command = original_commands[1]
        elif failure_kind == "unknown-prefix":
            command = "UNOWNED=1 " + command
        raise case.make_error(command)

    monkeypatch.setattr(pool, "run_resource_worker_processes", run_pool)


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("failure_kind", ["frontend", "backend", "unprefixed", "unknown-prefix"])
def test_host_split_failure_uses_only_actual_frontend_command_binding(
    coordinator_case, monkeypatch, failure_kind, terminator,
):
    case = coordinator_case
    case.payloads[0] = "ERR\twrong peer failure\n"
    case.payloads[1] = "ERR\tValueError: unsupported ordinary operation" + terminator
    _configure_host_indexed_split(case, monkeypatch, failure_kind)
    with pytest.raises(RuntimeError) as caught:
        case.run()
    detail = ": ValueError: unsupported ordinary operation" if failure_kind == "frontend" else ""
    assert str(caught.value) == "parallel frontend codegen worker failed" + detail
    assert caught.value.__cause__ is case.error
    assert "CPython" not in str(caught.value)


@pytest.mark.parametrize("payload", [None, b"ERR\t\xff\n", "success"])
def test_host_split_failure_keeps_generic_error_for_unusable_result(
    coordinator_case, monkeypatch, payload,
):
    case = coordinator_case
    case.payloads = ["ERR\twrong peer failure\n", payload]
    _configure_host_indexed_split(case, monkeypatch, "frontend")
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == "parallel frontend codegen worker failed"
    assert caught.value.__cause__ is case.error


def test_successful_host_split_still_collects_published_objects(coordinator_case, monkeypatch):
    case = coordinator_case
    case.payloads = ["success", "success"]
    case.succeed = True
    _configure_host_indexed_split(case, monkeypatch, "frontend")
    result = case.run()
    assert result[0] == [("first", ""), ("second", "")]
    assert [(name, kind) for name, kind, _path in result[7]] == [("first", "PCO"), ("second", "PCO")]


@pytest.mark.parametrize("in_process", [False, True])
def test_successful_workers_still_collect_normal_results(coordinator_case, monkeypatch, in_process):
    case = coordinator_case
    monkeypatch.setenv("PCC_PY_FRONTEND_IN_PROCESS_CODEGEN", "1" if in_process else "0")
    case.payloads = ["success", "success"]
    case.succeed = True
    assert case.run()[0] == [("first", "; module 0"), ("second", "; module 1")]
    assert len(case.calls) == 2


@pytest.mark.parametrize("payload,detail", [
    ("ERR\tValueError: unsupported ordinary operation\n", ": ValueError: unsupported ordinary operation"),
    ("ERR\tValueError: unsupported ordinary operation\r\n", ": ValueError: unsupported ordinary operation"),
    ("ERR\tValueError: embedded\rcontrol\r\n", ""),
    (None, ""), (b"ERR\t\xff\n", ""), ("success", ""),
])
def test_in_process_failure_reports_its_result_and_restores_environment(
    coordinator_case, monkeypatch, payload, detail,
):
    import os

    case = coordinator_case
    monkeypatch.setenv("PCC_PY_FRONTEND_IN_PROCESS_CODEGEN", "1")
    case.payloads[0] = payload
    case.failed_index = 0
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert str(caught.value) == "in-process frontend codegen worker failed" + detail
    assert len(case.calls) == 1
    assert os.environ["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] == "1"
    assert os.environ["PCC_PY_FRONTEND_JOBS"] == "2"


def _primary_resource_error_case(tmp_path, monkeypatch, primary, *, cleanup_at=None,
                                 diagnostic_failure="", diagnostic_error=None):
    """Use the real cleanup owner with fake children, signals and clocks only."""
    events = []
    output = []
    launched = []
    reports = []
    cleanup_error = subprocess.TimeoutExpired("fixture cleanup", 2)
    if diagnostic_error is None:
        diagnostic_error = OSError("diagnostic stream unavailable")

    class Stream:
        def write(self, text):
            events.append(("write",))
            if diagnostic_failure == "write":
                raise diagnostic_error
            output.append(text)

        def flush(self):
            events.append(("flush",))
            if diagnostic_failure == "flush":
                raise diagnostic_error

    class Process:
        returncode = None

        def __init__(self, pid):
            self.pid = pid

        def wait(self, timeout):
            events.append(("wait", self.pid, timeout))
            if self.pid == cleanup_at:
                raise cleanup_error
            self.returncode = 0
            return 0

    def start(specs, index):
        pid = 1001 + index
        launched.append(pid)
        pool._HOST_WORKERS[pid] = Process(pid)
        return pid

    def poll(pid):
        if primary is not None:
            return pool._WORKER_RUNNING
        pool._HOST_WORKERS[pid].returncode = 0
        return 0

    def owner_rss():
        if primary is not None and len(launched) == 3:
            raise primary
        return 1024 ** 2

    def read_report(path, pid, token):
        reports.append((path, token))
        return ("complete", 1024 ** 2, 1024 ** 2)

    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    monkeypatch.setattr(pool, "sys", SimpleNamespace(
        implementation=SimpleNamespace(name="cpython"), platform="linux", stderr=Stream(),
    ))
    monkeypatch.setattr(pool, "_HOST_WORKERS", {})
    monkeypatch.setattr(pool, "_start_resource_worker", start)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(pool.os, "killpg",
                        lambda pid, number: events.append(("signal", pid, number)), raising=False)
    monkeypatch.setattr(pool.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", owner_rss)
    monkeypatch.setattr(policy, "resource_task_order", lambda *args, **kwargs: ([0, 1, 2], []))
    monkeypatch.setattr(policy, "choose_task", lambda ready, *args, **kwargs: (ready[0], 1024 ** 2, False))
    monkeypatch.setattr(policy, "read_tree_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(policy, "read_worker_resource", read_report)
    commands = ["PRIVATE_VALUE=do-not-log /never-executed --secret-argument"] * 3
    tasks = [dict(report_path=str(tmp_path / ("private-report-" + str(index))),
                  estimate_bytes=1024 ** 2, inputs=[1], **{"class": "host:test"})
             for index in range(3)]

    def run():
        return pool.run_resource_worker_processes(
            commands, tasks, 3, 1024 ** 3, observations=[],
        )

    return SimpleNamespace(run=run, events=events, output=output, launched=launched,
                           reports=reports, cleanup_error=cleanup_error)


def _expected_resource_cleanup(cleanup_at=None):
    events = []
    for pid in (1001, 1002, 1003):
        events.extend((("signal", pid, 9), ("wait", pid, 2)))
        if pid == cleanup_at:
            break
        events.append(("wait", pid, 2))  # Existing retirement also waits.
    return events


def test_real_singleton_refusal_evidence_survives_cleanup_timeout(tmp_path, monkeypatch):
    case = _primary_resource_error_case(
        tmp_path, monkeypatch, policy.WorkerMemoryError("not reached"), cleanup_at=1001,
    )
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: 1024 ** 2)
    monkeypatch.setattr(
        policy, "read_tree_state",
        lambda _path, _budget, owner, active, *_args, **_kwargs:
        (0, {owner: 1024 ** 2, **{pid: 1024 ** 3 for pid in active}}),
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        case.run()
    assert caught.value is case.cleanup_error
    primary = caught.value.__context__
    assert isinstance(primary, policy.WorkerMemoryError)
    assert "live worker current subtree exceeds safe worker space: task=0" in str(primary)
    assert "current_worker_subtree_rss_bytes=1073741824" in str(primary)
    assert "full-task peak is unknown; tree cap is unchanged" in str(primary)
    assert case.output == [
        "pcc frontend primary resource error before cleanup: WorkerMemoryError="
        + repr(str(primary)) + "\n"
    ]
    assert case.launched == [1001]
    assert case.events == [("write",), ("flush",)] + _expected_resource_cleanup(1001)


@pytest.mark.parametrize("cleanup_at", [None, 1001, 1002, 1003])
def test_primary_resource_evidence_preserves_error_and_cleanup_order(
    tmp_path, monkeypatch, cleanup_at,
):
    primary = policy.WorkerMemoryError("live worker current subtree exceeds safe worker space: task=2")
    case = _primary_resource_error_case(tmp_path, monkeypatch, primary, cleanup_at=cleanup_at)
    expected = primary if cleanup_at is None else case.cleanup_error
    with pytest.raises(type(expected)) as caught:
        case.run()
    assert caught.value is expected
    if cleanup_at is not None:
        assert caught.value.__context__ is primary
    assert case.launched == [1001, 1002, 1003]
    assert case.events == [("write",), ("flush",)] + _expected_resource_cleanup(cleanup_at)
    assert case.output == [
        "pcc frontend primary resource error before cleanup: WorkerMemoryError="
        + repr(str(primary)) + "\n"
    ]
    # A bare re-raise must not add a handler re-raise frame to the primary.
    frames = []
    traceback = primary.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert frames.count("run_resource_worker_processes") == 1
    assert frames[-1] == "owner_rss"
    text = "".join(case.output)
    for value in ("do-not-log", "secret-argument", "private-report-", *[
        token for _path, token in case.reports
    ]):
        assert value not in text


@pytest.mark.parametrize("cleanup_at", [None, 1002])
@pytest.mark.parametrize("failure", ["format", "write", "flush"])
def test_primary_resource_diagnostic_failure_cannot_replace_original_outcome(
    tmp_path, monkeypatch, cleanup_at, failure,
):
    class UnformattableError(policy.WorkerMemoryError):
        def __str__(self):
            raise ValueError("diagnostic formatting failed")

    primary = UnformattableError() if failure == "format" else policy.WorkerMemoryError("primary")
    case = _primary_resource_error_case(
        tmp_path, monkeypatch, primary, cleanup_at=cleanup_at, diagnostic_failure=failure,
    )
    expected = primary if cleanup_at is None else case.cleanup_error
    with pytest.raises(type(expected)) as caught:
        case.run()
    assert caught.value is expected
    if cleanup_at is not None:
        assert caught.value.__context__ is primary
    diagnostic_events = {"format": [], "write": [("write",)], "flush": [("write",), ("flush",)]}
    assert case.events == diagnostic_events[failure] + _expected_resource_cleanup(cleanup_at)


@pytest.mark.parametrize("message", ["ordinary", "\n\r\t\x00\x1b\x85\u2028\u2029", "\U0001f600" * 4096, "\U0010ffff" * 4096])
def test_primary_resource_diagnostic_is_one_bounded_escaped_line(tmp_path, monkeypatch, message):
    primary = policy.WorkerMemoryError(message)
    case = _primary_resource_error_case(tmp_path, monkeypatch, primary)
    with pytest.raises(policy.WorkerMemoryError) as caught:
        case.run()
    assert caught.value is primary
    expected = ("pcc frontend primary resource error before cleanup: WorkerMemoryError="
                + repr(message[:2048]) + (" [truncated]" if len(message) > 2048 else "") + "\n")
    assert case.output == [expected]
    assert len(expected.splitlines()) == 1
    # repr of one codepoint needs at most ten UTF-8 bytes (a U escape).
    assert len(expected.encode("utf-8")) <= 70 + 2 + 2048 * 10 + len(" [truncated]\n")


@pytest.mark.parametrize("primary", [None, RuntimeError("other failure")])
def test_no_primary_resource_error_adds_no_diagnostic_calls(tmp_path, monkeypatch, primary):
    case = _primary_resource_error_case(tmp_path, monkeypatch, primary)
    if primary is None:
        assert case.run() is None
        assert case.events == [("wait", pid, 2) for pid in (1001, 1002, 1003)]
    else:
        with pytest.raises(RuntimeError) as caught:
            case.run()
        assert caught.value is primary
        assert case.events == _expected_resource_cleanup()
    assert case.output == []


@pytest.mark.parametrize("control", [KeyboardInterrupt(), SystemExit(7)])
def test_primary_resource_diagnostic_does_not_swallow_control_flow(tmp_path, monkeypatch, control):
    primary = policy.WorkerMemoryError("primary")
    case = _primary_resource_error_case(
        tmp_path, monkeypatch, primary, diagnostic_failure="write", diagnostic_error=control,
    )
    with pytest.raises(type(control)) as caught:
        case.run()
    assert caught.value is control
    assert case.events == [("write",)] + _expected_resource_cleanup()

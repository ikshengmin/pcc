"""Shared execution checks for owned-runtime source regressions.

Use the existing archive/compiler fixtures and process-group timeout helper.
Callers supply an explicit source-matched archive; these regressions never
provision a runtime or compiler. Native compiler parameters require a current
pcc1 selected by the existing python_program_compiler fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import pytest

from tests.python.process_timeout import run_process_group_timeout


_IR_MODULE_LIMIT = 2 * 1024 * 1024
_IR_TOTAL_LIMIT = 2 * 1024 * 1024
_IR_JSON_LIMIT = 2 * 1024 * 1024


def select_program_ir(modules, marker):
    """Select an observed program dump by its unique string, never order.

    Return the module index for retain_dump_evidence=True. Existing check-only
    callbacks, including their None return, keep their original path unchanged.
    Module scopes stay separate.
    A dump can be a legal prefix: producer completeness remains unproven.
    """
    if modules and any(isinstance(module, bytes) for module in modules):
        from tests.python.indexed_receipt_support import select_indexed_program_ir

        return select_indexed_program_ir(modules, marker)
    selected = []
    for index, module in enumerate(modules):
        # pcc.ir.ir.Constant._format_i8_array escapes every byte as \\HH;
        # GlobalVariable.render emits the standalone constant below.
        constants = re.findall(
            r'^(@[^\s=]+) = [^;\n]*\bconstant \[([0-9]+) x i8\] '
            r'c"((?:\\[0-9a-fA-F]{2})*)"(?:, align [0-9]+)?\s*$',
            module, re.MULTILINE,
        )
        functions = re.findall(r"^define [^\n]+\{\n(.*?)^\}", module,
                               re.MULTILINE | re.DOTALL)
        for symbol, count, payload in constants:
            literal = bytes.fromhex(payload.replace("\\", ""))
            if literal not in (marker.encode("utf-8"), marker.encode("utf-8") + b"\0"):
                continue
            assert len(literal) == int(count), "INVALID: marker array length differs"
            # A comment, declaration, or another module's identically named
            # internal global cannot supply the program's function use.
            use = re.compile(re.escape(symbol) + r"(?![-A-Za-z0-9_.$])")
            assert any(use.search(line.split(";", 1)[0]) for body in functions
                       for line in body.splitlines()), "MISSING: marker has no local function use"
            _assert_observed_module_closed(module)
            selected.append(index)
            break
    assert selected, "MISSING: program IR marker"
    assert len(selected) == 1, "AMBIGUOUS: program IR marker"
    return tuple(selected)


def _assert_observed_module_closed(module):
    """Reject obvious truncation, not certify the producer's complete module."""
    opened = False
    for line in module.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("define "):
            assert not opened and stripped.endswith("{"), "MALFORMED: incomplete function header"
            opened = True
        elif stripped == "}":
            assert opened, "MALFORMED: unmatched function end"
            opened = False
        if stripped.startswith("@"):
            assert not stripped.endswith("="), "MALFORMED: incomplete global"
            if ' c"' in stripped:
                assert re.search(r' c"(?:\\[0-9a-fA-F]{2}|[^"\\])*"', stripped), (
                    "MALFORMED: incomplete string constant"
                )
    assert not opened, "MALFORMED: unterminated function"


def _stream_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded_dump(path, expected_size):
    parts, digest, size = [], hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            assert size <= expected_size, "IDENTITY_CHANGED: dump grew after inventory"
            parts.append(chunk)
            digest.update(chunk)
    assert size == expected_size, "IDENTITY_CHANGED: dump shrank after inventory"
    return b"".join(parts), digest.hexdigest()


def _record_observed_ir_dump(dump, check, receipt, source, binary, archive, *, completed,
                             selected_required=True):
    """Retain bounded bytes observed in this invocation's fresh dump directory.

    Legacy text dumps have no producer-completion receipt. Indexed input
    receipts require independently rehashed invocation, emission and link data.
    Neither representation qualifies native execution or the final root proof.
    """
    evidence = {
        "schema": "pcc.observed-executable-ir-dump.v1",
        "status": "UNAVAILABLE",
        "compiler_completed": completed,
        "producer_completeness": "UNPROVEN",
        "all_modules_captured": "UNPROVEN",
        "qualification": "NOT_ASSESSED",
        "run_id": receipt["run_id"],
        "compiler_fixture_parameter": receipt["compiler_fixture_parameter"],
        "compiler_mode": receipt["compiler_mode"],
        "backend": receipt["backend"],
        "source_sha256": receipt["source_sha256"],
        "runtime_archive": receipt["runtime_archive"],
        "runtime_sha256": receipt["runtime_sha256"],
        "binary_path": receipt["binary_path"],
        "scope": "observed dump file bytes from this compiler invocation; not complete input or final frame/root proof",
        "limits_bytes": {"module": _IR_MODULE_LIMIT, "total_observed_ir": _IR_TOTAL_LIMIT,
                         "json_with_metadata": _IR_JSON_LIMIT},
        "modules": [],
    }
    if not completed:
        evidence["compile_error"] = receipt.get("error", "")[:400]
    try:
        emitted = []
        with os.scandir(dump) as entries:
            for entry in entries:
                assert len(emitted) < 128, "OVERSIZE: observed IR module inventory"
                emitted.append(Path(entry.path))
        emitted.sort()
        assert emitted, "MISSING: executable compiler produced no self-backend IR receipt"
        assert all(path.is_file() and not path.is_symlink() for path in emitted), "MALFORMED: dump file kind"
        if any(path.suffix == ".pidx" or path.name in ("invocation.json", "link.json")
               for path in emitted):
            from tests.python.indexed_receipt_support import (
                indexed_evidence_shell,
                read_indexed_inputs,
                validate_indexed_modules,
            )

            evidence["schema"] = "pcc.observed-executable-indexed-input.v1"
            evidence["representation"] = "indexed-codec"
            assert completed, "MISSING: indexed compilation did not complete"
            captured = read_indexed_inputs(
                dump, receipt, binary, _read_bounded_dump,
                module_limit=_IR_MODULE_LIMIT, total_limit=_IR_TOTAL_LIMIT,
                json_limit=_IR_JSON_LIMIT, expected_passes=(),
                selected_required=selected_required,
            )
            evidence = indexed_evidence_shell(
                receipt, module_limit=_IR_MODULE_LIMIT, total_limit=_IR_TOTAL_LIMIT,
                json_limit=_IR_JSON_LIMIT, selected_required=selected_required,
            )
            observed = {"source_sha256": _stream_sha256(source),
                        "runtime_sha256": _stream_sha256(archive)}
            evidence["observed_identity"] = observed
            assert all(observed[key] == receipt[key] for key in observed), (
                "IDENTITY_CHANGED: source or runtime changed during compilation"
            )
            evidence["binary_sha256"] = _stream_sha256(binary)
            evidence["retention"] = captured["retention"]
            evidence["observed_dump_sha256"] = captured["inventory"]
            # The full wrapper was already budgeted before raw reads/base64.
            # Keep an exact final check before the semantic callback as well.
            assert len(json.dumps(evidence).encode("utf-8")) + 1 <= _IR_JSON_LIMIT, (
                "OVERSIZE: JSON bytes including metadata and escaping"
            )
            validate_indexed_modules(captured["raw_modules"], _IR_MODULE_LIMIT)
            selected = check(captured["raw_modules"])
            if selected_required:
                assert isinstance(selected, tuple) and len(selected) == 1, "MALFORMED: select one program dump"
                index = selected[0]
                assert type(index) is int and 0 <= index < len(captured["raw_modules"]), "MALFORMED: dump index"
                assert captured["invocation"]["modules"][index]["source_sha256"] == receipt["source_sha256"], (
                    "IDENTITY_CHANGED: selected program source"
                )
                evidence["selected_module_indices"] = [index]
            evidence["observed_dump_sha256"] = captured["inventory"]
            receipt["observed_ir_dump_sha256"] = captured["inventory"]
            receipt["executable_input_ir"] = captured["inventory"]
            receipt["executable_input_representation"] = "indexed-codec-v1"
            evidence["producer_completeness"] = "LINK_BOUND"
            evidence["all_modules_captured"] = "LINK_BOUND"
            evidence["status"] = "INDEXED_INPUTS_RETAINED"
            return
        assert {path.name for path in emitted} == {
            "self_backend_input_" + str(index) + ".ll" for index in range(len(emitted))
        }, "MALFORMED: missing or unexpected dump module name"
        sizes = [path.stat().st_size for path in emitted]
        # Bound all input before reading any module or calling the selector,
        # including modules that will not be retained. No runtime IR spill.
        assert all(size <= _IR_MODULE_LIMIT for size in sizes), "OVERSIZE: observed IR module bytes"
        assert sum(sizes) <= _IR_TOTAL_LIMIT, "OVERSIZE: total observed IR bytes"
        raw_modules, inventory = [], {}
        for path, size in zip(emitted, sizes):
            raw, digest = _read_bounded_dump(path, size)
            raw_modules.append(raw)
            inventory[path.name] = digest
        receipt["observed_ir_dump_sha256"] = inventory
        evidence["observed_dump_sha256"] = inventory
        observed = {"source_sha256": _stream_sha256(source),
                    "runtime_sha256": _stream_sha256(archive)}
        evidence["observed_identity"] = observed
        assert all(observed[key] == receipt[key] for key in observed), (
            "IDENTITY_CHANGED: source or runtime changed during compilation"
        )
        if completed:
            assert binary.is_file(), "MISSING: compiler did not emit an executable"
            evidence["binary_sha256"] = _stream_sha256(binary)
        try:
            # Exactly the universal-newline normalization used by the existing
            # check-only hook. Retention below uses raw CRLF-preserving text.
            modules = [raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                       for raw in raw_modules]
        except UnicodeDecodeError as failure:
            raise AssertionError("MALFORMED: dump is not UTF-8") from failure
        selected = check(modules)
        if selected_required:
            assert isinstance(selected, tuple) and len(selected) == 1, "MALFORMED: select one program dump"
            index = selected[0]
            assert type(index) is int and 0 <= index < len(emitted), "MALFORMED: dump index"
            path, raw = emitted[index], raw_modules[index]
            evidence["modules"] = [{
                "name": path.name, "sha256": inventory[path.name],
                "bytes": len(raw), "text": raw.decode("utf-8"),
            }]
        else:
            receipt["executable_input_ir"] = inventory
        evidence["status"] = "DUMP_FILES_RETAINED" if completed else "INCOMPLETE_COMPILE"
        assert len(json.dumps(evidence).encode("utf-8")) + 1 <= _IR_JSON_LIMIT, (
            "OVERSIZE: JSON bytes including metadata and escaping"
        )
    except Exception as failure:
        reason = str(failure).split(":", 1)[0]
        evidence["status"] = reason if reason in (
            "MISSING", "AMBIGUOUS", "OVERSIZE", "IDENTITY_CHANGED", "MALFORMED",
        ) else "UNAVAILABLE"
        evidence["modules"] = []
        evidence.pop("retention", None)
        evidence.pop("selected_module_indices", None)
        evidence["producer_completeness"] = "UNPROVEN"
        evidence["all_modules_captured"] = "UNPROVEN"
        evidence["error"] = type(failure).__name__ + ": " + str(failure)[:400]
        raise
    finally:
        path = source.parent / "executable-ir-dump.json"
        payload = (json.dumps(evidence) + "\n").encode("utf-8")
        assert len(payload) <= _IR_JSON_LIMIT, "OVERSIZE: evidence metadata bytes"
        path.write_bytes(payload)
        receipt["executable_ir_dump_evidence"] = {
            "path": path.name, "sha256": hashlib.sha256(payload).hexdigest(),
            "status": evidence["status"],
        }


@pytest.fixture
def explicit_owned_runtime(request, monkeypatch):
    selected = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert selected, "Set PCC_RUNTIME_ARCHIVE to a source-matched owned archive"
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setenv("PCC_TEST_COMPILER_STRICT", "1")
    archive = request.getfixturevalue("pcc_runtime_archive")
    assert archive == Path(selected).resolve(strict=True)

    # The shared fixture verifies archive members and runtime source hashes.
    # Also bind it to the code generator under test before any compilation.
    from pcc.tools.runtime_archive_provenance import (
        manifest_is_stale_for_current_codegen,
    )

    manifest = json.loads(Path(str(archive) + ".provenance.json").read_text())
    assert not manifest_is_stale_for_current_codegen(manifest), (
        "Selected runtime was built by a different code generator"
    )
    for name, value in {
        "PCC_RUNTIME_CC": "pcc",
        "PCC_RUNTIME_HIGH": "py",
        "PCC_SELF_LINK": "pcc",
        "PCC_SELF_OBJ": "pcc",
        "PCC_IR_TO_OBJ_EMITTER": "pcc",
        "PCC_PYTHON_IR_PASSES": "off",
        "PCC_DISABLE_PY_RUN_CACHE": "1",
        "PCC_HOST_PYTHON": "/nonexistent/host-python",
        "PCC_HOST_PCC": "/nonexistent/host-pcc",
        "PATH": "",
    }.items():
        monkeypatch.setenv(name, value)
    return archive


def _record_execution(directory, label, command, environment):
    start_ns = time.monotonic_ns()
    result = run_process_group_timeout(command, env=environment, timeout=10)
    end_ns = time.monotonic_ns()
    (directory / (label + ".stdout")).write_text(result.stdout)
    (directory / (label + ".stderr")).write_text(result.stderr)
    record = {
        "command": list(command),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "elapsed_ns": end_ns - start_ns,
    }
    (directory / (label + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    return record


def assert_reference_program(program, expected, directory, *, arguments=()):
    source = directory / "program.py"
    source.write_text(program)
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    result = _record_execution(
        directory, "reference", [sys.executable, str(source), *arguments], environment,
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, expected, "",
    ), result
    return source, result


def assert_owned_program(
    program,
    expected,
    directory,
    compiler,
    compiler_mode,
    archive,
    capfd,
    *,
    provenance_probe=None,
    backends=tuple(range(5)),
    reference_arguments=(),
    emitted_ir_check=None,
    retain_dump_evidence=False,
):
    assert not retain_dump_evidence or emitted_ir_check is not None
    backends = tuple(backends)
    assert backends and len(backends) == len(set(backends))
    assert all(type(backend) is int and 0 <= backend < 5 for backend in backends)
    source, oracle = assert_reference_program(
        program, expected, directory, arguments=reference_arguments,
    )
    binary = directory / "program.out"
    requested_compiler_mode = compiler_mode
    if compiler.__module__ == "tests.pcc1_route":
        # Whole-suite native routing can replace the nominal pcc0 fixture.
        compiler_mode = "pcc1-routed"
    receipt = {
        "status": "RUNNING",
        "run_id": os.environ.get("PCC_NATIVE_OBJECT_GATE_RUN_ID", ""),
        "compiler_fixture_parameter": requested_compiler_mode,
        "compiler_mode": compiler_mode,
        "backend": "self",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_path": str(binary.resolve()),
        "runtime_archive": str(archive),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "reference": oracle,
        "gc_backends": list(backends),
        "executions": [],
    }
    receipt_path = directory / "ownership-regression.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    dump, ir_attempted, compiler_started = None, False, False
    try:
        # Capture the IR consumed by this executable emission, not a second
        # host/native --emit-llvm invocation with merely matching source.
        with pytest.MonkeyPatch.context() as patch:
            if emitted_ir_check is not None:
                assert not binary.exists(), "Refuse stale executable IR/binary evidence"
                fresh_dump = directory / "executable-ir"
                fresh_dump.mkdir()  # Fresh and empty: stale output is never evidence.
                dump = fresh_dump
                patch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump))
                receipt["indexed_input_invocation"] = uuid.uuid4().hex
                patch.setenv("PCC_DEBUG_SELF_IR_INVOCATION", receipt["indexed_input_invocation"])
                receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            compiler_started = True
            compiler(
                str(source), str(binary), backend="self", ir_scaffold_mode="on",
                runtime_archive=str(archive),
            )
        if emitted_ir_check is not None:
            ir_attempted = True
            _record_observed_ir_dump(
                dump, emitted_ir_check, receipt, source, binary, archive,
                completed=True, selected_required=retain_dump_evidence,
            )
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        if emitted_ir_check is not None and compiler_started and dump is not None and not ir_attempted:
            try:
                _record_observed_ir_dump(dump, emitted_ir_check, receipt, source, binary,
                                         archive, completed=False,
                                         selected_required=retain_dump_evidence)
            except Exception as diagnostic_error:
                # Preserve the original compile exception, including when no
                # dump exists or the evidence itself cannot be written.
                receipt["ir_capture_error"] = (
                    type(diagnostic_error).__name__ + ": " + str(diagnostic_error)[:400]
                )
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (directory / "compiler-wrapper.stdout").write_text(captured.out)
        (directory / "compiler-wrapper.stderr").write_text(captured.err)
    assert binary.is_file(), "Compiler did not emit an executable"
    # compile_python and the native CLI have no libpython selector: their
    # current pipeline is strict. Establish the output property independently
    # rather than copying that intended policy into a successful receipt.
    from scripts.owned_image_linkage import inspect_owned_image

    try:
        linkage = inspect_owned_image(binary.read_bytes())
    except Exception as error:
        receipt["status"] = "LINKAGE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    receipt["linkage"] = linkage
    receipt["libpython"] = linkage["libpython"]
    receipt["binary_sha256"] = linkage["binary_sha256"]
    for backend in backends:
        environment = dict(
            os.environ,
            PCC_GC_BACKEND=str(backend),
            PCC_TEST_NO_NATIVE_PROVISIONING="1",
            PCC_NO_AUTO_PCC1="1",
            PCC_HOST_PYTHON="/nonexistent/host-python",
            PCC_HOST_PCC="/nonexistent/host-pcc",
            PATH="",
        )
        environment.pop("LC_ALL", None)
        if provenance_probe is not None:
            environment["PCC_GC_REFCOUNT_PROVENANCE_PROBE"] = provenance_probe
        result = _record_execution(
            directory, f"gc{backend}", [str(binary)], environment,
        )
        receipt["executions"].append({"gc_backend": backend, **result})
        matches = all(result[key] == oracle[key] for key in (
            "returncode", "stdout", "stderr",
        ))
        if not matches:
            receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert matches, (compiler_mode, backend, result)
    receipt["status"] = "PASS"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

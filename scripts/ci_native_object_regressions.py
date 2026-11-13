#!/usr/bin/env python3
"""Bounded host-pcc0 object regressions using the terminal Stage1 runtime.

Seven common executable compiles and thirty native executions, plus one Win64
callback/root compile and five executions, with no archive/compiler provisioning.
The two-root split adds two executable compiles and five executions within the
unchanged 300-second total budget; budget fit must be established in CI.
This independent gate does not qualify Stage1, pcc1 or a self-host fixed point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ci_worker_size_prior_gate import (
    TWO_ROOT_CLEANUP_INPUTS,
    WIN64_DIRECT_SCALAR_INPUTS,
    WIN64_DIRECT_SCALAR_TEST,
    environment,
    run_pytest,
    runtime_identity,
    save,
    sha,
    source_identity,
)


NAME = "native-object-regressions"
TIMEOUT_S = 300
NODES = (
    "tests/python/test_object_type_identity.py::test_object_identity_native_five_gc[pcc0]",
    "tests/python/test_super_call_result_roots.py::"
    "test_super_result_native_matches_cpython_under_five_collectors",
    "tests/python/test_dict_method_result_ownership.py::"
    "test_dict_method_result_owners_native_five_gc[pcc0-copy]",
    "tests/python/test_dict_method_result_ownership.py::"
    "test_dict_method_result_owners_native_five_gc[pcc0-pop]",
    "tests/python/test_slot_call_exception_cleanup.py::"
    "test_two_root_cleanup_error_path_native_five_gc[pcc0]",
)
WINDOWS_NODES = (
    WIN64_DIRECT_SCALAR_TEST + "::test_native_win64_home_space_and_rooted_callback_five_collectors",
)
TEST_INPUTS = (
    *(node.split("::")[0] for node in (*NODES, *WINDOWS_NODES)),
    *TWO_ROOT_CLEANUP_INPUTS,
    *WIN64_DIRECT_SCALAR_INPUTS,
    "tests/python/owned_regression_support.py",
    "tests/python/process_timeout.py",
    "tests/python/conftest.py",
    "tests/conftest.py",
    "tests/runtime_fixture_provenance.py",
    "tests/test_ci_native_object_regressions.py",
    "conftest.py",
    "pyproject.toml",
)
WINDOWS_SYSTEM_LIBRARIES = {
    "kernel32.dll", "ws2_32.dll", "ntdll.dll", "shell32.dll", "psapi.dll", "bcrypt.dll", "advapi32.dll",
}


def selected_nodes(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    return NODES + (WINDOWS_NODES if platform_name == "win32" else ())


def expected_programs():
    from tests.python.test_dict_method_result_ownership import (
        _program as dict_method_program,
    )
    from tests.python.test_object_type_identity import PROGRAM
    from tests.python.test_slot_call_exception_cleanup import TWO_ROOT_PROGRAM
    from tests.python.test_super_call_result_roots import NATIVE_SOURCE
    from tests.python.two_root_lifecycle_support import TRACING_TWO_ROOT_PROGRAM

    return {
        "object-identity": (PROGRAM, "OBJECT_TYPE_IDENTITY_OK\n"),
        "super-result": (NATIVE_SOURCE, "5\nTrue\ncaught\n"),
        "dict-copy-result": (dict_method_program("copy"), "DICT_METHOD_OWNERS_OK\n"),
        "dict-pop-result": (dict_method_program("pop"), "DICT_METHOD_OWNERS_OK\n"),
        "two-root-cleanup": (TWO_ROOT_PROGRAM, "TWO_ROOT_CLEANUP_EXCEPTION_OK\n"),
        "two-root-tracing": (TRACING_TWO_ROOT_PROGRAM, "TWO_ROOT_TRACING_CLEANUP_OK\n"),
    }


def verify_native_image(binary):
    from scripts.verify_nolibpython import linked_libraries

    payload = binary.read_bytes()
    if payload[:4] == b"\x7fELF":
        from pcc.backend.elf_x86_64 import parse_static_executable

        # Both ELF targets must have no interpreter or dynamic program header.
        parse_static_executable(payload)
        return {"format": "static-elf", "libraries": []}
    assert payload[:2] == b"MZ", "expected a native Linux or Windows executable"
    libraries = linked_libraries(payload)
    assert libraries and set(libraries) <= WINDOWS_SYSTEM_LIBRARIES, libraries
    return {"format": "pe", "libraries": libraries}


def read_receipt(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, "duplicate receipt key: " + key
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def expected_backends(label):
    return [0] if label == "two-root-cleanup" else (
        [1, 2, 3, 4] if label == "two-root-tracing" else list(range(5))
    )


def verify_executions(record, binary, backends, output, *, strict=False):
    if strict:
        assert record["gc_backends"] == backends
        assert all(type(gc) is int for gc in record["gc_backends"])
    executions = record["executions"]
    assert [row["gc_backend"] for row in executions] == backends
    for row in executions:
        assert type(row["gc_backend"]) is int
        assert row["command"] == [str(binary)]
        assert (row["returncode"], row["stdout"], row["stderr"]) == (0, output, "")
    return [
        {"requested_gc_backend": row["gc_backend"],
         **{key: value for key, value in row.items() if key != "gc_backend"}}
        for row in executions
    ]


def verify_common_identity(record, source, binary, runtime, run_id, *, strict=False):
    assert record["status"] == "PASS"
    if strict:
        assert run_id and record["run_id"] == run_id, "stale native execution receipt"
    assert record["compiler_fixture_parameter"] == record["compiler_mode"] == "pcc0"
    assert record["backend"] == "self"
    assert record["source_sha256"] == sha(source)
    assert record["runtime_archive"] == runtime["path"]
    assert record["runtime_sha256"] == runtime["hashes"][""]
    if strict:
        assert binary.is_absolute() and record["binary_path"] == str(binary)
    assert record["binary_sha256"] == sha(binary)


def verify_two_root_ir(directory, record, label):
    from tests.python.two_root_lifecycle_support import assert_compiler_two_root_error_edge

    dump = directory / "executable-ir"
    paths = sorted(dump.iterdir())
    assert paths and all(path.is_file() and not path.is_symlink() for path in paths)
    assert all(re.fullmatch(r"self_backend_input_[0-9]+\.ll", path.name) for path in paths)
    assert {path.name for path in paths} == {
        "self_backend_input_" + str(index) + ".ll" for index in range(len(paths))
    }, "missing or duplicate executable IR module"
    inventory = {path.name: sha(path) for path in paths}
    assert record["executable_input_ir"] == inventory, "executable IR inventory differs"
    assert len(set(inventory.values())) == len(inventory), "duplicate executable IR module"
    modules = [path.read_text(encoding="utf-8") for path in paths]
    # These distinct emitted string constants bind the collection mode to
    # its own executable input rather than accepting the other's fail edge.
    markers = {"two-root-cleanup": "TWO_ROOT_CLEANUP_EXCEPTION_OK",
               "two-root-tracing": "TWO_ROOT_TRACING_CLEANUP_OK"}
    # The owned IR renderer escapes every i8 byte as \HH. Decode the
    # string payloads, preserving module boundaries for the verifier below.
    literals = []
    for module in modules:
        for payload in re.findall(r'c"((?:[^"\\]|\\[0-9a-fA-F]{2})*)"', module):
            literals.append(re.sub(r"\\([0-9a-fA-F]{2})",
                                   lambda match: chr(int(match[1], 16)), payload))
    assert any(markers[label] in literal for literal in literals), "wrong two-root executable IR source"
    assert all(not any(marker in literal for literal in literals)
               for key, marker in markers.items() if key != label)
    assert_compiler_two_root_error_edge(modules)


def expected_slot_object(ir_text, target):
    from pcc.backend.owned_object_emit import emit_owned_object

    return emit_owned_object(ir_text, target)


def verify_two_root_slots(directory, runtime, run_id, lifecycle_root):
    from tests.python.two_root_lifecycle_support import SLOT_PROBE_PROGRAM, cleanup_slots_ir

    paths = sorted(directory.rglob("slot-postcondition.json"))
    assert len(paths) == 1, "missing or duplicate two-root slot postcondition receipt"
    path = paths[0]
    assert path.parent == lifecycle_root / "slots", "unpaired two-root slot receipt"
    record = read_receipt(path)
    assert record["schema"] == "pcc.two-root-slots.v1"
    source, binary = path.parent / "slots.py", path.parent / "slots.out"
    assert source.read_text(encoding="utf-8") == SLOT_PROBE_PROGRAM
    verify_common_identity(record, source, binary, runtime, run_id, strict=True)
    assert record["target"] == runtime["target"]
    assert record["probe_object_producer"] == "host-pcc-owned-object-emitter"
    probe_ir, helper = path.parent / "slots.ll", path.parent / "slots.o"
    expected_ir = cleanup_slots_ir(runtime["target"])
    assert probe_ir.read_bytes() == expected_ir.encode("utf-8"), "wrong slot postcondition IR"
    assert record["probe_ir_sha256"] == sha(probe_ir)
    assert helper.read_bytes() == expected_slot_object(expected_ir, runtime["target"])
    assert record["probe_object_sha256"] == sha(helper)
    linkage = record["linkage"]
    assert linkage["schema"] == "pcc.owned-image-linkage.v1"
    assert linkage["libpython"] == "off" and linkage["binary_sha256"] == sha(binary)
    executions = verify_executions(record, binary, list(range(5)), "TWO_ROOT_SLOTS_OK\n", strict=True)
    return {"receipt": str(path), **record, "image": verify_native_image(binary),
            "executions": executions}


def verify_program_receipts(directory, runtime, run_id):
    expected = expected_programs()
    paths = sorted(directory.rglob("ownership-regression.json"))
    assert len(expected) == len(NODES) + 1, "native node/source manifest count differs"
    assert len(paths) <= len(expected), "unexpected native execution receipt count"
    results, two_root_directories = {}, {}
    for path in paths:
        record = read_receipt(path)
        source = path.parent / "program.py"
        binary = path.parent / "program.out"
        # Windows writes CRLF; text binds source semantics while the digest
        # still binds its exact bytes on that runner.
        program = source.read_text(encoding="utf-8")
        labels = [label for label, (text, _output) in expected.items() if text == program]
        assert len(labels) == 1, "unexpected or duplicate source-program receipt"
        label = labels[0]
        _program, output = expected.pop(label)
        split = label in ("two-root-cleanup", "two-root-tracing")
        verify_common_identity(record, source, binary, runtime, run_id, strict=split)
        oracle = record["reference"]
        arguments = ["cyclic-reference"] if label == "two-root-tracing" else []
        if split:
            assert oracle["command"] == [sys.executable, str(source), *arguments]
        assert (oracle["returncode"], oracle["stdout"], oracle["stderr"]) == (0, output, "")
        if label in ("two-root-cleanup", "two-root-tracing"):
            name = "gc0" if label == "two-root-cleanup" else "tracing"
            assert path.parent.name == name, "swapped two-root receipt directory"
            two_root_directories[label] = path.parent.parent
            verify_two_root_ir(path.parent, record, label)
        executions = verify_executions(record, binary, expected_backends(label), output, strict=split)
        results[label] = {"receipt": str(path), **record,
                          "image": verify_native_image(binary), "executions": executions}
    assert not expected, "missing native execution receipts: " + ", ".join(sorted(expected))
    assert len(set(two_root_directories.values())) == 1, "unpaired two-root lifecycle receipts"
    results["two-root-slots"] = verify_two_root_slots(
        directory, runtime, run_id, two_root_directories["two-root-cleanup"],
    )
    return results


def verify_win64_callback_receipt(directory, runtime, run_id):
    from pcc.backend.target_objects import encode_assembly_object
    from tests.python.test_windows_direct_scalar_call_args import (
        HOME_CALLBACK_ASM,
        MANAGED_CALLBACK_SOURCE,
        TARGET,
    )

    paths = sorted(directory.rglob("win64-home-callback.json"))
    assert len(paths) == 1, "missing or duplicate Win64 callback receipt"
    path = paths[0]
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema"] == "pcc.win64-home-callback.v1"
    assert record["status"] == "PASS"
    assert run_id and record["run_id"] == run_id, "stale Win64 callback receipt"
    assert record["compiler_mode"] == "host-pcc0"
    assert record["backend"] == "self"
    assert record["target"] == runtime["target"] == TARGET
    source = path.parent / "win64_home_callback.py"
    helper = path.parent / "win64_home_callback.obj"
    binary = path.parent / "win64_home_callback.exe"
    assert source.read_text(encoding="utf-8") == MANAGED_CALLBACK_SOURCE
    assert record["source_sha256"] == sha(source)
    assert record["helper_assembly_sha256"] == hashlib.sha256(HOME_CALLBACK_ASM.encode()).hexdigest()
    assert helper.read_bytes() == encode_assembly_object(HOME_CALLBACK_ASM, TARGET)
    assert record["helper_sha256"] == sha(helper)
    assert record["runtime_archive"] == runtime["path"]
    assert record["runtime_sha256"] == runtime["hashes"][""]
    assert binary.is_absolute() and record["binary_path"] == str(binary)
    assert record["binary_sha256"] == sha(binary)
    image = verify_native_image(binary)
    assert image["format"] == "pe", "Win64 callback must execute a native PE image"
    with binary.open("rb") as stream:
        header = stream.read(64)
        assert len(header) == 64 and header[:2] == b"MZ"
        stream.seek(int.from_bytes(header[60:64], "little"))
        assert stream.read(6) == b"PE\0\0\x64\x86", "Win64 callback must target AMD64"
    executions = record["executions"]
    assert [row["requested_gc_backend"] for row in executions] == list(range(5))
    for row in executions:
        assert type(row["requested_gc_backend"]) is int
        assert row["command"] == [str(binary)]
        assert (row["returncode"], row["stdout"], row["stderr"]) == (
            0, "WIN64_HOME_ROOT_CALLBACK_OK\n", "",
        )
    return {
        "receipt": str(path), **record, "image": image,
        "observed_collector_witness": True,
        "collector_evidence": "The exact native source asserts pcc_gc_backend() equals "
                              "PCC_GC_BACKEND before the home-space, result-616 and rooted-callback oracle.",
    }


def run(out):
    out = Path(out).resolve()
    nodes = selected_nodes()
    run_id = uuid.uuid4().hex
    receipt_path = out / (NAME + ".json")
    assert not receipt_path.exists(), "refuse a stale native object gate receipt"
    receipt = {
        "schema": "pcc.native-object-regressions.v1",
        "status": "RUNNING",
        "compiler_mode": "host-pcc0",
        "qualifies_stage1": False,
        "nodes": list(nodes),
        "run_id": run_id,
        "timeout_s": TIMEOUT_S,
        "tree_rss_limit_bytes": 17179869184,
        "observed_collector_witness": False,
        "collector_evidence": "requested_gc_backend records are PCC_GC_BACKEND requests; "
                              "collector selection is not independently observed",
    }
    save(receipt_path, receipt)
    try:
        assert sys.platform.startswith("linux") or sys.platform == "win32"
        env = dict(environment())
        env.pop("LC_ALL", None)
        # Stage1 enables timing telemetry, but these behavioral regressions
        # also assert quiet compilation. Keep diagnostics enabled for Stage1
        # itself and disable only its opt-in timing in this child environment.
        env.pop("PCC_PY_FRONTEND_WORKER_TIMING", None)
        env.update(PCC_TEST_NO_NATIVE_PROVISIONING="1", UV_PYTHON_DOWNLOADS="never",
                   PCC_NATIVE_OBJECT_GATE_RUN_ID=run_id)
        selected = env.get("PCC_RUNTIME_ARCHIVE")
        assert selected, "the existing Stage1 runtime must be admitted before this gate"
        archive = Path(selected)
        assert archive.is_absolute() and archive.is_file(), "admitted runtime archive is missing"
        source = source_identity()
        assert source["clean_commit"] is True and source["commit"] == os.environ["GITHUB_SHA"]
        stage = json.loads((out / "stage1.json").read_text(encoding="utf-8"))
        assert stage["phase"] == "stage1" and stage["status"] in ("PASS", "FAIL")
        assert stage["source"] == source, "source, Python or locked test tools differ from Stage1"
        runtime = runtime_identity(archive)
        assert runtime == stage["runtime"], "runtime does not match the terminal Stage1 receipt"
        inputs = {name: sha(ROOT / name) for name in TEST_INPUTS}
        receipt.update(source=source, runtime=runtime, test_inputs=inputs,
                       stage1_status=stage["status"])
        save(receipt_path, receipt)
        try:
            run_pytest(out, NAME, nodes, env, integration=True, timeout=TIMEOUT_S)
            receipt["programs"] = verify_program_receipts(out / NAME, runtime, run_id)
            if WINDOWS_NODES[0] in nodes:
                receipt["programs"]["win64-home-callback"] = verify_win64_callback_receipt(
                    out / NAME, runtime, run_id,
                )
        finally:
            receipt["source_after"] = source_identity()
            receipt["runtime_after"] = runtime_identity(archive)
            assert receipt["source_after"] == source, "source changed during native tests"
            assert receipt["runtime_after"] == runtime, "runtime changed during native tests"
            assert {name: sha(ROOT / name) for name in TEST_INPUTS} == inputs
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(receipt_path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.out_dir)


if __name__ == "__main__":
    main()

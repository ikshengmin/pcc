"""Finite offline consumer tests: real owned ELF/map/PCO, modeled launch receipts."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.frontends.python.pipeline_frontend_workers import write_worker_manifest
from scripts import ci_stage1_function_map as transport
from tests.python.test_elf_x86_64 import _exit_42_object
from tests.test_ci_stage1_function_map import _receipt_fixture, _json, LEASE, SOURCE

MODULE = "pcc.frontends.c.passes.context"
INDEX = 1
PID = 431
PHASE = "frontend_generate"


def _owner():
    return importlib.import_module("scripts.pcc_worker_cpu_pc_report")


def _file(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _sample(path, header, pcs):
    path.write_text(json.dumps(header) + "\n" + "".join(str(pc) + "\n" for pc in pcs))


def _fixture(tmp_path):
    image, mapping, stage, shared, manifest_bundle = _receipt_fixture(tmp_path)
    binding = _json(tmp_path / "binding.json", transport.make_binding(image, mapping, stage, shared, manifest_bundle, LEASE))
    rows = [json.loads(line) for line in mapping.read_text().splitlines()[1:]]
    names = {row["name"]: row for row in rows}
    start = names["_start"]["start"]
    answer = names["answer"]["start"]
    # Real ELF RX header/padding address without a function and unmapped VA.
    pcs = [start, start, answer, start - 1, 1]
    work = tmp_path / "worker"; work.mkdir()
    ast_dir = work / "ast"; ast_dir.mkdir()
    ir_dir = work / "artifacts"; ir_dir.mkdir()
    source0 = work / "entry.py"; source0.write_text("pass\n")
    source = work / "context.py"; source.write_text("def bump():\n    pass\n")
    ast = _json(ast_dir / "module_1.json", {"fixture": "modeled AST bytes"})
    exports = _json(work / "native_exports.json", {"fixture": "modeled export bytes"})
    manifest = work / "worker.manifest"
    result = work / "worker.tsv"
    write_worker_manifest(str(manifest), str(result), str(ir_dir), str(exports), str(ast_dir),
                          [str(source0), str(source)], ["entry", MODULE], [INDEX], entry_module="entry",
                          sibling_inits=(MODULE,), ir_scaffold_mode="on", verbose=True)
    artifact = ir_dir / "module_1.direct.pco"
    artifact.write_bytes(elf.emit_relocatable(_exit_42_object()))
    result.write_text(f"OK\t{INDEX}\t{MODULE}\t0\t0\t0\t{ir_dir / 'module_1.ll'}\t1\t2\t3\tPCO\t{artifact}\n")
    sample_path = ir_dir / f"worker-cpu-pc-{INDEX}-{PID}.samples"
    header = {"schema": "pcc.worker-cpu-pc.v1", "module": MODULE, "index": INDEX, "pid": PID,
              "phase": PHASE, "period_ns": 2_000_000, "capacity": 8192, "count": len(pcs), "dropped": 0,
              "compiler_image_sha256": _file(image)["sha256"], "source_commit": SOURCE["commit"],
              "source_sha256": SOURCE["source_sha256"], "manifest_sha256": _file(manifest)["sha256"],
              "status": "ok", "phase_complete": True}
    _sample(sample_path, header, pcs)
    argv = [str(image), "--pcc-python-multi-codegen-worker", str(manifest)]
    guard = _json(work / "guard.json", {"schema": "pcc.process_tree_sample.v1", "status": "COMPLETE", "returncode": 0,
        "command": ["/owned/launcher", *argv], "largest_process_observed": {"pid": PID, "command": " ".join(argv),
            "manifest_paths": [str(manifest)]}})
    launch = {"schema": "pcc.worker-cpu-pc-launch.v1", "status": "ok", "producer": LEASE, "source": SOURCE,
              "compiler": {**_file(image), "post_sha256": _file(image)["sha256"]},
              "map_binding_sha256": _file(binding)["sha256"], "argv": argv, "pid": PID, "module": MODULE,
              "index": INDEX, "phase": PHASE, "manifest": _file(manifest),
              "inputs": {"ast": _file(ast), "exports": _file(exports), "source": _file(source)},
              "sample": {**_file(sample_path), "count": len(pcs), "dropped": 0},
              "terminal": {"returncode": 0, "guard": _file(guard), "result": _file(result),
                           "artifact": {**_file(artifact), "kind": "PCO"}}}
    launch_path = _json(work / "launch.json", launch)
    files = dict(image_path=image, map_path=mapping, binding_path=binding, stage_receipt_path=stage,
                 shared_receipt_path=shared, bundle_manifest_path=manifest_bundle, lease=LEASE,
                 launch_path=launch_path, manifest_path=manifest, ast_path=ast, exports_path=exports,
                 source_path=source, sample_path=sample_path, guard_path=guard, result_path=result, artifact_path=artifact)
    return files, launch, header, pcs, names


def _rewrite_sample(files, launch, header, pcs):
    _sample(files["sample_path"], header, pcs)
    launch["sample"] = {**_file(files["sample_path"]), "count": header["count"], "dropped": header["dropped"]}
    _json(files["launch_path"], launch)


def test_actual_map_unique_alias_unknown_ranges(tmp_path):
    files, launch, header, pcs, names = _fixture(tmp_path)
    report = _owner().build_report(**files)
    assert report["status"] == "ok"
    assert report["counts"] == {"stored": 5, "dropped": 0, "unique": 2, "ambiguous": 1, "unmapped_rx": 1, "outside_rx": 1}
    assert report["unique_functions"][0]["samples"] == 2
    assert report["unique_functions"][0]["function"]["name"] == "_start"
    alias = report["ambiguous_pcs"][0]
    assert alias["samples"] == 1 and alias["candidate_count"] == 2
    assert {row["name"] for row in alias["candidates"]} == {"answer", "answer_alias"}
    assert not report["capture_censored"]
    assert report["module"] == MODULE and report["phase"] == PHASE
    assert report["compiler_sha256"] == launch["compiler"]["sha256"]
    assert report["sample_sha256"] == launch["sample"]["sha256"]


def test_alias_cap_does_not_select_a_false_owner(tmp_path, monkeypatch):
    files, *_ = _fixture(tmp_path)
    owner = _owner(); monkeypatch.setattr(owner, "MAX_RETAINED_ALIAS_ROWS", 1)
    report = owner.build_report(**files)
    alias = report["ambiguous_pcs"][0]
    assert alias["candidate_count"] == 2 and alias["candidates"] == []
    assert report["counts"]["ambiguous"] == 1


def test_dropped_samples_are_censored_and_empty_capture_is_not_attribution(tmp_path):
    files, launch, header, pcs, _ = _fixture(tmp_path)
    # Actual cap overflow represents only the captured prefix, never the
    # entire phase. The tiny map addresses remain real; sample stream modeled.
    full = (pcs * 1640)[:8192]
    header["count"] = len(full); header["dropped"] = 17
    _rewrite_sample(files, launch, header, full)
    report = _owner().build_report(**files)
    assert report["capture_censored"] and report["counts"]["dropped"] == 17
    assert sum(report["counts"][key] for key in ("unique", "ambiguous", "unmapped_rx", "outside_rx")) == 8192
    header["count"] = header["dropped"] = 0
    _rewrite_sample(files, launch, header, [])
    empty = _owner().build_report(**files)
    assert empty["status"] == "no_samples" and not empty["unique_functions"]


@pytest.mark.parametrize("damage", ["refused", "phase-incomplete", "zero-pc", "noncanonical-pc", "too-wide-pc", "count",
    "dropped", "wrong-pid", "wrong-phase", "wrong-module", "wrong-index", "wrong-image", "wrong-source", "wrong-manifest", "extra-header"])
def test_sample_rejects_unqualified_or_malformed_actual_rows(tmp_path, damage):
    files, launch, header, pcs, _ = _fixture(tmp_path)
    if damage == "refused": header["status"] = "unavailable"
    elif damage == "phase-incomplete": header["phase_complete"] = False
    elif damage == "zero-pc": pcs[0] = 0
    elif damage == "noncanonical-pc": pcs[0] = "0001"
    elif damage == "too-wide-pc": pcs[0] = 1 << 64
    elif damage == "count": header["count"] += 1
    elif damage == "dropped": header["dropped"] = -1
    elif damage == "wrong-pid": header["pid"] += 1
    elif damage == "wrong-phase": header["phase"] = "backend_call"
    elif damage == "wrong-module": header["module"] = "other"
    elif damage == "wrong-index": header["index"] += 1
    elif damage == "wrong-image": header["compiler_image_sha256"] = "0" * 64
    elif damage == "wrong-source": header["source_sha256"] = "0" * 64
    elif damage == "wrong-manifest": header["manifest_sha256"] = "0" * 64
    else: header["unexpected"] = 1
    _rewrite_sample(files, launch, header, pcs)
    with pytest.raises(ValueError): _owner().build_report(**files)


@pytest.mark.parametrize("damage", ["launch-refused", "failed-terminal", "wrong-lease", "wrong-clean-source", "wrong-post-image", "wrong-binding",
    "wrong-argv", "wrong-sample-hash", "wrong-guard", "guard-pid", "result-error", "wrong-artifact-kind", "manifest-multiple", "wrong-ast", "wrong-exports", "wrong-source-contents", "wrong-pco", "wrong-sample-path"])
def test_independent_launch_and_actual_input_output_binding(tmp_path, damage):
    files, launch, header, pcs, _ = _fixture(tmp_path)
    if damage == "launch-refused": launch["status"] = "unavailable"
    elif damage == "failed-terminal": launch["terminal"]["returncode"] = 1
    elif damage == "wrong-lease": launch["producer"] = {**LEASE, "run_attempt": LEASE["run_attempt"] + 1}
    elif damage == "wrong-clean-source": launch["source"] = {**SOURCE, "clean_commit": False}
    elif damage == "wrong-post-image": launch["compiler"]["post_sha256"] = "0" * 64
    elif damage == "wrong-binding": launch["map_binding_sha256"] = "0" * 64
    elif damage == "wrong-argv": launch["argv"] = ["/different", *launch["argv"][1:]]
    elif damage == "wrong-sample-hash": launch["sample"]["sha256"] = "0" * 64
    elif damage in ("wrong-guard", "guard-pid"):
        guard = json.loads(files["guard_path"].read_text())
        if damage == "wrong-guard": guard["status"] = "TIMEOUT"
        else: guard["largest_process_observed"]["pid"] += 1
        _json(files["guard_path"], guard); launch["terminal"]["guard"] = _file(files["guard_path"])
    elif damage == "result-error":
        files["result_path"].write_text("ERR\tfailed\n"); launch["terminal"]["result"] = _file(files["result_path"])
    elif damage == "wrong-artifact-kind": launch["terminal"]["artifact"]["kind"] = "ASM"
    elif damage == "manifest-multiple":
        p = files["manifest_path"]; text = p.read_text(); assert text.endswith("1\n1\n")
        p.write_text(text[:-4] + "2\n0\n1\n"); launch["manifest"] = _file(p)
        header["manifest_sha256"] = launch["manifest"]["sha256"]; _rewrite_sample(files, launch, header, pcs)
    elif damage == "wrong-sample-path": launch["sample"]["path"] += ".partial"
    else:
        path = {"wrong-ast": "ast_path", "wrong-exports": "exports_path", "wrong-source-contents": "source_path", "wrong-pco": "artifact_path"}[damage]
        with files[path].open("ab") as stream: stream.write(b"changed")
    _json(files["launch_path"], launch)
    with pytest.raises(ValueError): _owner().build_report(**files)

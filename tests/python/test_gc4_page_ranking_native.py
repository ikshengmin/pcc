"""Execute the current emitted selector with an isolated raw-ABI C harness.

This regression needs no pcc1, runtime archive, cached object, or historical
source. It does not exercise managed GC or certify a full-runtime bootstrap.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import pytest

from tests.python import test_freestanding_gc_relocation_selector as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
ORACLE = json.loads((FIXTURES / "gc4_page_ranking_expected.json").read_text())
pytestmark = [
    pytest.mark.xdist_group("gc4-selector-native-rank"),
    pytest.mark.pcc_gate(
        probe=lambda: sys.platform.startswith("linux")
        and platform.machine().lower() in ("x86_64", "amd64")
        and shutil.which("cc") is not None
        and shutil.which("nm") is not None,
    ),
]

# Use the production single-module frontend settings and runtime pass list.
# Never call build_runtime_archive/ensure_target_runtime: no runtime is linked.
EMIT_MODULE = r"""
from pathlib import Path
import hashlib, json, sys
from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_ir_passes
from pcc.ir.optimization.driver import optimize_ir
from pcc.backend.owned_object_emit import emit_owned_object
source, output = map(Path, sys.argv[1:])
triple = 'x86_64-unknown-linux-gnu'
ir = output.with_suffix('.ll')
_compile_runtime_module('freestanding_gc_relocation_selector', str(source), str(ir), triple)
passes = runtime_ir_passes(str(source.parent.parent))
text = optimize_ir(ir.read_text(), passes)
ir.write_text(text)
output.write_bytes(emit_owned_object(text, triple))
print(json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'object_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                  'runtime_passes': passes, 'target': triple}))
"""


@pytest.fixture(scope="module")
def emitted_selector(tmp_path_factory):
    build = tmp_path_factory.mktemp("gc4-page-ranking-native")
    obj = build / "selector.o"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PCC_TEST_NO_NATIVE_PROVISIONING"] = "1"
    compile_result = subprocess.run(
        [sys.executable, "-c", EMIT_MODULE, str(contract.STRICT_SOURCE), str(obj)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    (build / "compile.log").write_text(compile_result.stdout + compile_result.stderr)
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    (build / "harness_globals.h").write_text(
        "\n".join("uint64_t " + name + ";" for name in sorted(contract.RAW_GLOBAL_IMPORTS))
        + "\n"
    )
    executable = build / "selector-harness"
    link = subprocess.run(
        ["cc", "-O2", "-I", str(build),
         str(FIXTURES / "gc4_page_ranking_native.c"), str(obj),
         "-Wl,--wrap=malloc", "-Wl,--wrap=free", "-o", str(executable)],
        capture_output=True, text=True, timeout=15,
    )
    (build / "link.log").write_text(link.stdout + link.stderr)
    assert link.returncode == 0, link.stdout + link.stderr
    return build, obj, executable


@pytest.mark.parametrize("case", ORACLE["cases"], ids=lambda c: "-".join(map(str, c["argv"])))
def test_current_native_selector_preserves_page_semantics(emitted_selector, case):
    build, _obj, executable = emitted_selector
    argv = case["argv"]
    result = subprocess.run(
        [str(executable), *map(str, argv)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    with (build / "results.jsonl").open("a") as stream:
        stream.write(json.dumps({"case": argv, "observed": observed}) + "\n")
    expected = dict(zip(ORACLE["fields"], case["expected"], strict=True))
    assert {name: observed[name] for name in ORACLE["fields"]} == expected
    assert observed["rank_live"] == observed["lock_errors"] == 0
    if argv[4] or argv[5] or argv[3] <= 1 or argv[0].endswith("busy"):
        assert observed["pin_checks"] == case["original_pin_checks"]
    if argv[0] == "scale":
        # Measured calls from native scoring/add, not a Python algorithm model.
        assert observed["pin_checks"] <= 4 * argv[1] * argv[2]
    if argv[0] == "residual" and argv[4] == 0:
        # 39 fresh owners: ten full preflights + page commit + residual scan.
        # All residual scores are -1 (the score function never returns zero),
        # so no winner may be rescored and increment this a second time.
        assert observed["fresh"] == 39 * (10 + 1 + 1)
        assert observed["selected"] == 114


def test_current_owned_object_has_exact_strict_closure(emitted_selector):
    _build, obj, _executable = emitted_selector
    undefined = subprocess.check_output(["nm", "-u", str(obj)], text=True, timeout=10)
    assert {line.split()[-1] for line in undefined.splitlines()} == (
        contract.RAW_FUNCTION_IMPORTS | contract.RAW_GLOBAL_IMPORTS
    )
    defined = subprocess.check_output(
        ["nm", "-g", "--defined-only", str(obj)], text=True, timeout=10,
    )
    assert {line.split()[-1] for line in defined.splitlines()} == (
        contract.OWNED_SYMBOLS | contract.OWNED_GLOBALS
    )


@pytest.mark.parametrize("mode,pages,owners,budget", [
    ("tie", 3, 4, 2),       # Equal page scores and a partial page budget.
    ("tie", 1, 48, 1),      # Multiple 16-owner chunks, including the seed slot.
    ("scale", 32, 32, 32),  # Unequal page priorities and a complete page budget.
])
def test_simulated_owned_epoch_preserves_order_and_scan_counts(
    emitted_selector, mode, pages, owners, budget,
):
    # Reuse the existing explicit semantic/order oracle, not a candidate or
    # timing baseline. Provider values simulate ownership; no world is stopped.
    build, _obj, executable = emitted_selector
    case = next(c for c in ORACLE["cases"]
                if c["argv"] == [mode, pages, owners, budget, 0, 0, 0])
    expected = dict(zip(ORACLE["fields"], case["expected"], strict=True))
    chunks = (pages * owners + 15) // 16
    for epoch in (-1, 11):
        argv = [mode, pages, owners, budget, 1, 0, 0, epoch]
        result = subprocess.run(
            [str(executable), *map(str, argv)], capture_output=True, text=True, timeout=10,
        )
        with (build / "epoch-results.jsonl").open("a") as stream:
            stream.write(json.dumps({"argv": argv, "returncode": result.returncode,
                                    "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        assert result.returncode == 0, result.stdout + result.stderr
        observed = json.loads(result.stdout)
        assert {name: observed[name] for name in ORACLE["fields"]} == expected
        assert observed["rank_allocs"] == 0  # Threaded mode never enables ranking.
        # Multi-page threaded calls attempt trailer admission once. These
        # legacy pages have no span, so a positive lease is rejected after
        # the entry, owner, and first-page checks; an unowned lease stops at entry.
        admission_queries = (3 if epoch > 0 else 1) if budget > 1 else 0
        assert observed["epoch_queries"] == budget * (2 if epoch > 0 else 1) + admission_queries
        # Count stub pin-provider/poll calls, not all visits or runtime latency.
        page_scans = budget * pages - budget * (budget - 1) // 2
        assert observed["pin_checks"] == (
            owners * (page_scans + budget * (2 if epoch > 0 else 3)) + 2 * budget
        )
        assert observed["polls"] == budget * (
            chunks + owners // 16 + 1 if epoch > 0 else 2 * chunks + owners // 16
        )


@pytest.fixture(scope="module")
def trailer_selector(emitted_selector):
    build, obj, _legacy = emitted_selector
    ir = obj.with_suffix(".ll").read_text()
    for symbol, expected in contract.OWNED_TRAILER_SIGNATURES.items():
        matches = re.findall(r"^define external (i64|ptr|void) @" + re.escape(symbol) + r"\(([^)]*)\)", ir, re.MULTILINE)
        assert len(matches) == 1, (symbol, matches)
        result_type, parameters = matches[0]
        arguments = [parameter.strip().split()[0] for parameter in parameters.split(",")] if parameters else []
        assert {"arguments": arguments, "return": result_type} == expected
    executable = build / "trailer-harness"
    link = subprocess.run(
        ["cc", "-O2", "-I", str(build), str(FIXTURES / "gc4_trailer_native.c"), str(obj),
         "-Wl,--wrap=malloc", "-Wl,--wrap=free", "-o", str(executable)],
        capture_output=True, text=True, timeout=15,
    )
    assert link.returncode == 0, link.stdout + link.stderr
    return executable


def _run_trailer(executable, *, shape="ordinary", mode="tie", pages=9, owners=17,
                 budget=20, threads=1, epoch=11, fail_at=0, fail_rank=0,
                 loss="none", replacement=-1):
    argv = [shape, mode, pages, owners, budget, threads, epoch, fail_at, fail_rank, loss, replacement]
    result = subprocess.run([str(executable), *map(str, argv)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (argv, result.stdout, result.stderr)
    observed = json.loads(result.stdout)
    for key in ("free_errors", "protect_errors", "integrity_errors", "lock_errors", "rank_live"):
        assert observed[key] == 0, (key, argv, observed)
    assert observed["distinct_spans"] == pages
    return observed


def _trailer_pair(executable, **kwargs):
    # Both execute the current production source. The unowned provider forces
    # the original registered-scan path; this is not a historical binary oracle.
    baseline = _run_trailer(executable, **dict(kwargs, epoch=-1))
    candidate = _run_trailer(executable, **kwargs)
    fields = ORACLE["fields"] + ["rank_allocs", "malloc_calls", "plan_attempts", "allocation_hash", "free_hash"]
    assert {key: candidate[key] for key in fields} == {key: baseline[key] for key in fields}
    assert baseline["initialized_seen"] == baseline["active_seen"] == 0
    assert baseline["trailer_sentinel"] == kwargs.get("pages", 9)
    return baseline, candidate


@pytest.mark.parametrize("shape", ["ordinary", "retained"])
def test_owned_trailer_explicit_tie_order_and_storage(trailer_selector, shape):
    _, observed = _trailer_pair(trailer_selector, shape=shape, pages=3, owners=4, budget=2)
    assert observed["selected"] == 8 and observed["page_n"] == 2
    assert observed["first_obj"] == 12 and observed["first_page"] == 2
    assert observed["reloc_hash"] == 15810070159895781251
    assert observed["page_hash"] == 11125486474286715500
    assert observed["initialized_seen"] == observed["active_seen"] == observed["trailer_clean"] == 3
    assert observed["logical_capacity"] == 4096
    assert observed["retained_extent"] == (65536 if shape == "retained" else 4096)


@pytest.mark.parametrize("shape", ["ordinary", "retained"])
def test_owned_trailer_full_multi_chunk_rank(trailer_selector, shape):
    fallback, observed = _trailer_pair(trailer_selector, shape=shape, mode="scale", pages=32, owners=32, budget=32)
    assert observed["selected"] == 1024 and observed["page_n"] == 32
    assert observed["initialized_seen"] == observed["active_seen"] == observed["trailer_clean"] == 32
    assert observed["pin_checks"] < fallback["pin_checks"]


@pytest.mark.parametrize("shape", ["no_span", "bad_capacity", "bad_extent", "bad_allocated", "bad_class", "unaligned", "overflow"])
def test_owned_trailer_invalid_last_page_falls_back(trailer_selector, shape):
    _, observed = _trailer_pair(trailer_selector, shape=shape)
    assert observed["selected"] > 0
    assert observed["initialized_seen"] == observed["active_seen"] == 0
    assert observed["trailer_sentinel"] == 9


@pytest.mark.parametrize("shape", ["ordinary", "retained"])
@pytest.mark.parametrize("fail_at", [1, 2, 5, 18, 19, 20, 76, 77, 80])
def test_owned_trailer_absolute_plan_oom_and_cleanup(trailer_selector, shape, fail_at):
    _, observed = _trailer_pair(trailer_selector, shape=shape, fail_at=fail_at)
    assert observed["malloc_calls"] == fail_at
    assert observed["initialized_seen"] == observed["trailer_clean"] == 9
    assert observed["rank_allocs"] == 0


@pytest.mark.parametrize("phase", ["admit", "initialize", "scan", "sort", "plan", "commit", "cleanup"])
@pytest.mark.parametrize("replacement", [-1, 0, 12])
def test_owned_trailer_epoch_loss_poisoned_storage(trailer_selector, phase, replacement):
    kwargs = dict(mode="tie", pages=32, owners=4, budget=32, shape="retained")
    fallback = _run_trailer(trailer_selector, **kwargs, epoch=-1)
    observed = _run_trailer(trailer_selector, **kwargs, loss=phase, replacement=replacement)
    fields = ORACLE["fields"] + ["rank_allocs", "malloc_calls", "plan_attempts", "allocation_hash", "free_hash"]
    assert {key: observed[key] for key in fields} == {key: fallback[key] for key in fields}
    assert observed["loss_fired"] == 1 and observed["poison_count"] == 32
    assert observed["final_epoch"] == replacement
    assert observed["selected"] == 128
    if phase not in {"admit", "initialize"}:
        assert observed["initialized_seen"] == 32 and observed["active_seen"] > 0
    if phase == "cleanup":
        assert observed["trailer_clean"] == 16 and observed["trailer_nonzero"] == 16

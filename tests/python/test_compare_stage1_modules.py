"""Offline parser controls only; no compiler, native execution or fixture IR."""

import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[2] / "scripts/research/compare_stage1_modules.py"
SPEC = importlib.util.spec_from_file_location("compare_stage1_modules", PATH)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def records(name="example", instructions=40, blocks=10, codegen=100):
    values = {
        "worker": f"index=7 infer_ms=3 codegen_ms={codegen}",
        "kernel": f"blocks={blocks} instructions={instructions} functions=2 call_cont_blocks=3 "
                  "call_err_cleanup_blocks=1 err_frame_blocks=1 inline_error_edges=0",
        "capture": "elapsed_ms=4 supported_records=50 fallback_records=0",
        "render": "elapsed_ms=0",
        "structured": "unscaled=20 move=30 call=15 fallback=0",
        "phase": "target=aarch64-unknown-linux-gnu route=indexed-native-sections clock_ok=1 "
                 "codegen_complete=1 frontend_generate_ns=100 backend_call_inclusive_ns=1000 "
                 "prepare_verify_ns=150 stackmap_plan_ns=200 function_emit_inclusive_ns=500 "
                 "function_setup_ns=100 assembly_parse_ns=0",
    }
    return [reader.PREFIXES[kind] + f"module={name} " + fields + "\n"
            for kind, fields in values.items()]


def test_interleaving_is_joined_by_name_not_worker_order_or_index():
    left, right = records("left"), records("right", instructions=80)
    parsed = reader.parse_log([line for pair in zip(left, right) for line in pair])
    assert parsed["left"]["kernel"]["instructions"] == 40
    assert parsed["right"]["kernel"]["instructions"] == 80
    assert parsed["left"]["worker"]["index"] == parsed["right"]["worker"]["index"]


def test_real_counter_names_keep_inclusive_timers_separate():
    report = reader.compare(reader.parse_log(records()),
                            reader.parse_log(records(instructions=20, blocks=11)))
    assert report["totals"]["ir_instructions"]["change_percent"] == -50
    assert report["totals"]["blocks"]["after"] == 11
    assert report["totals"]["function_emit_inclusive_ns"]["after"] == 500
    assert report["totals"]["function_setup_ns"]["after"] == 100
    assert report["totals"]["text_render_ms"]["change_percent"] is None


@pytest.mark.parametrize("damage", [
    lambda lines: lines[:-1],
    lambda lines: lines + lines[:1],
    lambda lines: [line.replace("clock_ok=1", "clock_ok=0") for line in lines],
    lambda lines: [line.replace("codegen_complete=1", "codegen_complete=0") for line in lines],
    lambda lines: [line.replace("instructions=40", "instructions=-1") for line in lines],
    lambda lines: [line.replace("instructions=40", "instructions=40 instructions=50") for line in lines],
])
def test_incomplete_duplicate_or_damaged_logs_fail_closed(damage):
    with pytest.raises(ValueError):
        reader.parse_log(damage(records()))


@pytest.mark.parametrize("before,after", [
    ("aarch64-unknown-linux-gnu", "x86_64-unknown-linux-gnu"),
    ("indexed-native-sections", "indexed-assembly"),
])
def test_changed_target_or_route_is_not_a_paired_performance_claim(before, after):
    old = reader.parse_log(records())
    new = reader.parse_log([line.replace(before, after) for line in records()])
    with pytest.raises(ValueError):
        reader.compare(old, new)


def test_missing_modules_stay_explicit_and_requested_missing_module_fails():
    old = reader.parse_log(records("shared") + records("removed"))
    new = reader.parse_log(records("shared") + records("added"))
    report = reader.compare(old, new)
    assert report["paired_modules"] == 1
    assert report["before_only_modules"] == ["removed"]
    assert report["after_only_modules"] == ["added"]
    with pytest.raises(ValueError):
        reader.compare(old, new, ["removed"])

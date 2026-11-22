"""scripts/pcc_perf.py: the pure parts of the performance entry point.

Pair statistics, the call-graph parser of a macOS ``sample`` report, the
category and graph-lock attribution, the primitive table and the ledger
format run here on synthetic inputs; the primitives themselves run under
CPython. Building an arm and running a compiled program need a runtime
build and are not exercised by this file.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("pcc_perf", ROOT / "scripts/pcc_perf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PERF = _load()
WAIT = PERF.LOCK_WAIT


def test_pause_subcommand_reports_one_quiescent_snapshot(tmp_path):
    from pcc.diagnostics import _gc_pause_layout as layout

    vector = [0] * layout.SLOTS
    vector[layout.WINDOWS:layout.INVALID] = [2, 3, 3]
    vector[layout.AGGREGATE] = vector[layout.LONGEST] = 3
    vector[layout.ZERO_US_WINDOWS] = 1
    snapshot = tmp_path / "native.json"
    snapshot.write_text(json.dumps({
        "gc": {"119": 2, "120": 4, "121": 3}, "pause_vector": vector,
    }))
    argv = [sys.executable, "-I", str(ROOT / "scripts" / "pcc_perf.py"),
            "pause", "report", str(snapshot)]
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["longest_window_us"]["runtime_other"] == 3
    assert report["total_us"] == 3 and report["public_telemetry"]["total_us"] == 4
    assert report["zero_us_windows"] == 1
    snapshot.write_text("gc-pause-stw 1 3 3\n")
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "public counters alone are insufficient" in result.stderr
    assert not result.stdout


def test_pause_prepare_dispatches_to_the_overlay_builder(tmp_path, monkeypatch):
    from pcc.diagnostics import gc_pause_probe

    calls = []
    monkeypatch.setattr(gc_pause_probe, "make_overlay", lambda *args: calls.append(args))
    checkout, out = tmp_path / "source", tmp_path / "diagnostic"
    assert PERF.main(["pause", "prepare", str(checkout), str(out)]) == 0
    assert calls == [(checkout, out)]


def _run(qps, stw=0.0, waits=0, wait_ms=0.0, cpu=0.0):
    return {"qps": qps, "requests": 10000, "stw_ms": stw, "sweep_ms": 0.0,
            "lock_waits": waits, "lock_wait_ms": wait_ms, "allocations": 0,
            "cpu_us": cpu}


def test_pairs_report_medians_change_and_wins():
    first = [_run(100, 10, 4, 2.0), _run(110, 12, 6, 3.0), _run(90, 11, 5, 2.5)]
    second = [_run(120, 5, 1, 0.5), _run(105, 6, 2, 1.0), _run(130, 7, 3, 1.5)]
    row = PERF.summarize_pairs(first, second)
    assert row["pairs"] == 3
    assert (row["qps_a"], row["qps_b"]) == (100, 120)
    assert row["change_percent"] == pytest.approx(20.0)
    # The second arm won the first and third pair, not the second.
    assert row["wins"] == 2
    assert (row["stw_ms_a"], row["stw_ms_b"]) == (11, 6)
    assert (row["lock_wait_ms_a"], row["lock_wait_ms_b"]) == (2.5, 1.0)
    assert (row["lock_waits_a"], row["lock_waits_b"]) == (5, 2)


def test_pairs_report_the_processor_time_of_a_request():
    first = [_run(100, cpu=30.0), _run(90, cpu=34.0), _run(110, cpu=32.0)]
    second = [_run(100, cpu=20.0), _run(95, cpu=22.0), _run(105, cpu=24.0)]
    row = PERF.summarize_pairs(first, second)
    assert (row["cpu_us_a"], row["cpu_us_b"]) == (32.0, 22.0)


def test_a_measured_child_reports_its_own_processor_time():
    process = PERF.MeasuredPopen(
        [sys.executable, "-c", "sum(i * i for i in range(300000))"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process.communicate(timeout=60)
    assert process.returncode == 0
    # A fraction of a second of work, and nothing like the parent's total.
    assert 0.0 < process._pcc_usage < 30.0
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        assert process._pcc_peak_rss_bytes > 0


def test_load_note_says_when_a_rate_cannot_be_trusted(monkeypatch):
    monkeypatch.setattr(PERF.os, "cpu_count", lambda: 8)
    assert PERF.load_note(1.5) == "load 1.5 on 8 cores"
    assert "read the processor time" in PERF.load_note(6.0)


def test_counters_named_by_the_tool_are_the_runtime_header_indexes():
    names = {}
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    for line in header.splitlines():
        text = line.strip().rstrip(",")
        if text.startswith("PCC_GC_COUNTER_") and "=" in text:
            name, value = (part.strip() for part in text.split("=", 1))
            if value.isdigit():
                names[int(value)] = name
    assert names[PERF.ALLOCATIONS] == "PCC_GC_COUNTER_ALLOCATIONS"
    assert names[PERF.STW_SUM_US] == "PCC_GC_COUNTER_COLLECTION_STW_SUM_US"
    assert [names[index] for index in PERF.SWEEP_PHASE_US] == [
        "PCC_GC_COUNTER_SWEEP_PASS0_SUM_US",
        "PCC_GC_COUNTER_SWEEP_PASS1_SUM_US",
        "PCC_GC_COUNTER_SWEEP_PASS2_SUM_US",
    ]
    assert all(names[index].startswith("PCC_GC_COUNTER_GRAPH_WAIT_")
               and names[index].endswith("_COUNT") for index in PERF.LOCK_WAIT_COUNT)
    assert all(names[index].startswith("PCC_GC_COUNTER_GRAPH_WAIT_")
               and names[index].endswith("_SUM_US") for index in PERF.LOCK_WAIT_US)


def test_the_handler_program_reports_every_counter_the_tool_reads():
    source = (ROOT / "benchmarks/handler/native_stats.py").read_text()
    assert "tuple(range(119, 158))" in source
    needed = (PERF.STW_SUM_US, *PERF.SWEEP_PHASE_US, *PERF.LOCK_WAIT_COUNT, *PERF.LOCK_WAIT_US)
    assert all(119 <= index < 158 for index in needed)


SAMPLE = """Analysis of sampling bench (pid 1) every 1 millisecond
Call graph:
    100 Thread_1   DispatchQueue_1: com.apple.main-thread  (serial)
    + 100 start  (in dyld) + 100  [0x1]
    +   100 main  (in bench) + 20  [0x100001020]
    +     60 work  (in bench) + 8  [0x100002008]
    +     ! 25 lock_for  (in bench) + 4  [0x100003004]
    +     ! : 25 wrong_name  (in bench) + 4  [0x100004004]
    +     ! 10 __psynch_cvwait  (in libsystem_kernel.dylib) + 8  [0x2]
    +     30 collect  (in bench) + 4  [0x100005004]
    +       30 sweep  (in bench) + 4  [0x100006004]

Total number in stack (recursive counted multiple, when >=5):

Binary Images:
       0x100000000 -        0x100ffffff +bench (0) <UUID> /tmp/bench
"""
SYMBOLS = [
    (0x100001000, "main"),
    (0x100002000, "work"),
    (0x100003000, "pcc_thread_graph_lock_acquire_for"),
    (0x100004000, WAIT),
    (0x100005000, "pcc_gc_collect_tracing"),
    (0x100006000, "pcc_gc_tracing_sweep_unreachable"),
]


def test_sample_frames_are_resolved_by_address_not_by_the_reported_name():
    rows = PERF.parse_sample(SAMPLE, SYMBOLS, "bench")
    names = [row[2] for row in rows]
    # ``sample`` printed wrong_name; the address belongs to the wait loop.
    assert WAIT in names and "wrong_name" not in names
    assert "pcc_gc_tracing_sweep_unreachable" in names
    assert names[1] == "start"  # another image keeps its own name


def test_self_time_is_split_by_the_owning_category():
    rows = PERF.parse_sample(SAMPLE, SYMBOLS, "bench")
    shares = PERF.category_shares(rows)
    # main 10 self and work 25 self are program time; the wait is the graph
    # lock's; the sweep belongs to the collection above it; idle is dropped.
    assert shares == {"program": 35, "graph-lock": 25, "collect": 30}


def test_gap_charges_gc3_worker_and_cell_registration_at_their_outer_boundary():
    rows = [
        [0, 12, "op_str_concat"],
        [1, 8, "pcc_gc_register_object_cell"],
        [2, 5, "pcc_thread_graph_lock_acquire_for"],
        [0, 20, "pcc_gc_cms_worker_main_py"],
        [1, 15, "pcc_gc_concurrent_sweep_slice"],
        [2, 6, "pcc_gc_register_object_cell"],
        [3, 3, "pcc_allocator_alloc_object"],
        [1, 2, "__psynch_cvwait"],
    ]
    # The worker owns its subtree, even when it registers a cell;
    # directly entered registration owns its lock/allocator work.
    assert PERF.category_shares(rows) == {"program": 4, "register": 8, "collect": 18}
    # Attribution cannot change the denominator or drop busy samples.
    assert sum(PERF.category_shares(rows).values()) == sum(PERF.function_shares(rows).values())


def test_gap_charges_a_mutator_marking_step_to_collection():
    # A mutator paying allocation debt marks through
    # pcc_gc_tracing_step_cycle from inside pcc_gc_alloc; that step is
    # collection work, while the allocation glue around it stays program.
    rows = [
        [0, 30, "op_str_concat"],
        [1, 26, "pcc_gc_alloc"],
        [2, 12, "pcc_gc_note_alloc"],
        [3, 9, "pcc_gc_cms_publish_alloc"],
        [4, 9, "pcc_gc_tracing_step_cycle"],
        [5, 7, "pcc_gc_page_mark_assign_cell"],
        [2, 10, "pcc_allocator_alloc_object"],
    ]
    assert PERF.category_shares(rows) == {"program": 11, "collect": 9, "allocator": 10}
    assert sum(PERF.category_shares(rows).values()) == sum(PERF.function_shares(rows).values())


def test_self_and_inclusive_samples_by_function():
    rows = PERF.parse_sample(SAMPLE, SYMBOLS, "bench")
    assert PERF.function_shares(rows) == {
        "main": 10, "work": 25, WAIT: 25, "pcc_gc_tracing_sweep_unreachable": 30,
    }
    inclusive = PERF.inclusive_shares(rows)
    # Idle samples are under nobody; a stack counts once for each frame.
    assert inclusive["main"] == 90 and inclusive["work"] == 50
    assert inclusive["pcc_thread_graph_lock_acquire_for"] == inclusive[WAIT] == 25
    assert inclusive["pcc_gc_collect_tracing"] == 30


def test_costs_split_the_unit_time_over_busy_samples():
    assert PERF.scaled({"a": 30, "b": 10}, 40, 2000.0) == {"a": 1500.0, "b": 500.0}
    # An inclusive share may exceed nothing: the whole profile is the unit.
    assert PERF.scaled({"root": 40}, 40, 2000.0) == {"root": 2000.0}


def test_top_rows_order_by_cost_or_by_largest_change():
    first = {"a": 5.0, "b": 3.0, "c": 1.0}
    assert PERF.top_rows(first, None, 2) == [("a", 5.0, None, None), ("b", 3.0, None, None)]
    second = {"a": 5.5, "c": 4.0, "d": 2.0}
    rows = PERF.top_rows(first, second, 3)
    assert rows == [("b", 3.0, 0.0, -3.0), ("c", 1.0, 4.0, 3.0), ("d", 0.0, 2.0, 2.0)]


def test_lock_waits_are_charged_to_the_function_that_asked():
    rows = PERF.parse_sample(SAMPLE, SYMBOLS, "bench")
    assert PERF.lock_callers(rows) == {"work": 25}


def test_ledger_rows_are_one_json_object_per_line(tmp_path, monkeypatch):
    monkeypatch.setattr(PERF, "HOME", tmp_path)
    rows = [{"gc": 3, "workers": 4, "qps_a": 1.0, "qps_b": 2.0, "pairs": 5}]
    PERF.append_ledger(rows)
    PERF.append_ledger(rows)
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == rows + rows


def test_a_screen_of_fewer_than_five_pairs_is_labelled(capsys):
    row = PERF.summarize_pairs([_run(100)], [_run(110)])
    row.update({"gc": 1, "workers": 1})
    PERF.print_comparison([row])
    assert "screen, not a result" in capsys.readouterr().out


def _row(gc, workers, first, second):
    row = PERF.summarize_pairs([_run(q) for q in first], [_run(q) for q in second])
    row.update({"gc": gc, "workers": workers})
    return row


def test_an_unreached_collector_sets_the_noise_floor_of_its_worker_count():
    # gc0 is the control: the arms differ only where gc0 never goes, so its
    # largest single-pair deviation is the noise floor of that worker count.
    rows = [
        _row(0, 1, [100] * 5, [104, 98, 101, 100, 99]),
        _row(3, 1, [100] * 5, [110, 109, 108, 111, 110]),
        _row(2, 1, [100] * 5, [103, 102, 101, 104, 103]),
        _row(0, 4, [100] * 5, [100, 101, 99, 100, 100]),
        _row(3, 4, [100] * 5, [97, 96, 98, 97, 95]),
    ]
    PERF.apply_control(rows, collector=0)
    verdicts = {(row["gc"], row["workers"]): (row["verdict"], round(row["noise_percent"], 1))
                for row in rows}
    assert verdicts == {
        (0, 1): ("control", 4.0), (3, 1): ("gain", 4.0), (2, 1): ("within noise", 4.0),
        (0, 4): ("control", 1.0), (3, 4): ("loss", 1.0),
    }


def test_a_change_inside_a_noise_floor_of_five_percent_or_more_is_inconclusive():
    rows = [
        _row(0, 4, [100] * 5, [118, 86, 104, 97, 101]),
        _row(3, 4, [100] * 5, [104, 103, 106, 102, 104]),
        _row(1, 4, [100] * 5, [125, 122, 121, 124, 123]),
    ]
    PERF.apply_control(rows, collector=0)
    assert [row["verdict"] for row in rows] == ["control", "inconclusive", "gain"]
    assert rows[1]["noise_percent"] == pytest.approx(18.0)


def test_a_duplicate_base_arm_gives_each_row_its_own_noise_floor():
    row = PERF.summarize_pairs(
        [_run(100)] * 5, [_run(q) for q in (106, 107, 105, 108, 106)],
        control=[_run(q) for q in (102, 99, 101, 98, 100)])
    row.update({"gc": 3, "workers": 1})
    PERF.apply_control([row])
    assert row["verdict"] == "gain"
    assert row["noise_percent"] == pytest.approx(2.0)


def test_without_a_control_a_comparison_cannot_count(capsys):
    rows = [_row(3, 1, [100] * 5, [110] * 5)]
    PERF.apply_control(rows)
    PERF.print_comparison(rows)
    assert rows[0]["verdict"] == "no control"
    assert "cannot count toward the freeze rule" in capsys.readouterr().out


def test_fewer_than_five_pairs_stay_a_screen_whatever_the_control_says():
    rows = [_row(0, 1, [100] * 3, [100] * 3), _row(3, 1, [100] * 3, [150] * 3)]
    PERF.apply_control(rows, collector=0)
    assert rows[1]["verdict"] == "screen"


def test_the_control_collector_must_be_measured():
    with pytest.raises(SystemExit):
        PERF.main(["ab", "a", "b", "--gc", "3", "--control", "0"])


def _record_order(monkeypatch, rates):
    order = []
    monkeypatch.setattr(PERF, "binary_of", lambda name, program="native_stats": name)

    def run(binary, collector, workers, rounds):
        order.append(binary)
        return _run(rates[binary])

    monkeypatch.setattr(PERF, "run_once", run)
    return order


def test_two_arms_alternate_their_order_every_pair(monkeypatch):
    order = _record_order(monkeypatch, {"a": 100, "b": 110})
    PERF.compare("a", "b", 2, (3,), (1,), 10)
    assert order == ["a", "b", "b", "a"]


def test_a_duplicate_base_arm_rotates_with_the_two_arms(monkeypatch):
    order = _record_order(monkeypatch, {"a": 100, "b": 110, "a2": 101})
    rows = PERF.compare("a", "b", 3, (3,), (1,), 10, control_arm="a2")
    assert order == ["a", "b", "a2", "b", "a2", "a", "a2", "a", "b"]
    assert rows[0]["control_ratios"] == [pytest.approx(1.01)] * 3


HANDLER = ROOT / "benchmarks/handler"
# What 64 operations of each pure primitive sum to, derived by hand from
# its loop (for example ``loop`` adds 0..63).
SUMS = {
    "loop": 2016, "range": 2016, "call": 2016, "kwcall": 6048, "method": 192,
    "attr": 2016, "instance": 64, "tuple": 64, "list": 64, "dict": 128,
    "dict_get": 128, "dict_set": 2, "list_append": 64, "list_iter": 544,
    "str_concat": 448, "int_str": 118, "fstring": 374, "encode": 64,
    "json": 5504, "closure": 2080, "try": 2080, "raise": 64,
    "generator": 2080, "clock": 64,
}


def test_the_primitive_table_names_every_primitive_once():
    operations = PERF._load_module(HANDLER / "primitive_ops.py").OPS
    names = [name for name, _ in PERF.PRIMITIVES]
    assert len(names) == len(set(names))
    assert set(names) == set(operations) | PERF.TASK_PRIMITIVES
    assert not set(operations) & PERF.TASK_PRIMITIVES
    batches = dict(PERF.PRIMITIVES)
    # These two count sixteen element operations per pass.
    assert batches["list_append"] % 16 == 0 and batches["list_iter"] % 16 == 0
    assert PERF.SMOKE_BATCH % 16 == 0
    source = (HANDLER / "primitives.py").read_text()
    for name in PERF.TASK_PRIMITIVES:
        assert f'if name == "{name}":' in source


@pytest.mark.parametrize("name", sorted(SUMS))
def test_a_pure_primitive_sums_what_its_loop_says(name):
    operations = PERF._load_module(HANDLER / "primitive_ops.py").OPS
    assert set(SUMS) == set(operations)
    assert operations[name](64) == SUMS[name]


@pytest.mark.parametrize("name", sorted(PERF.TASK_PRIMITIVES | {"json"}))
def test_the_cpython_driver_runs_a_primitive(name):
    out = subprocess.run(
        [sys.executable, str(HANDLER / "primitives_asyncio.py"), name, "16", "0"],
        capture_output=True, text=True, timeout=120, check=True,
    )
    data = json.loads(out.stdout)
    assert data["operations"] == 16
    assert data["checksum"] == (1376 if name == "json" else 16)
    assert data["seconds"] > 0


def test_smoke_cases_expect_what_cpython_computes(tmp_path):
    cases = PERF.smoke_cases("native_stats", tmp_path / "native_stats", HANDLER)
    assert len(cases) == (1 + len(PERF.PRIMITIVES)) * len(PERF.WORKERS)
    by_argv = {tuple(argv[1:]): expected for argv, expected in cases}
    assert by_argv[("20", "0", "3", "--summary", "4")] == {"requests": 60}
    assert by_argv[("prim", "kwcall", "64", "0", "1")] == {"operations": 64, "checksum": 6048}
    assert by_argv[("prim", "kwcall", "64", "0", "4")] == {"operations": 256, "checksum": 24192}
    assert by_argv[("prim", "spawn", "64", "0", "4")] == {"operations": 256, "checksum": 256}
    assert PERF.smoke_cases("sync_payload", tmp_path / "sync_payload", HANDLER) == [
        ([str(tmp_path / "sync_payload")], {"stdout": "2666\n"})]


def test_a_smoke_run_passes_only_with_the_expected_output():
    expected = {"operations": 64, "checksum": 2016}
    good = json.dumps({"operations": 64, "checksum": 2016, "seconds": 0.1})
    assert PERF.smoke_passed(0, good, "", expected)
    assert not PERF.smoke_passed(1, good, "", expected)
    assert not PERF.smoke_passed(0, good, "warning", expected)
    assert not PERF.smoke_passed(0, good.replace("2016", "2017"), "", expected)
    assert not PERF.smoke_passed(0, "Traceback", "", expected)
    assert PERF.smoke_passed(0, "2666\n", "", {"stdout": "2666\n"})
    assert not PERF.smoke_passed(0, "2667\n", "", {"stdout": "2666\n"})


def test_allocations_per_operation_cancel_the_start_up():
    # 9000 objects at start-up, 3 per operation; the timed run did one
    # warm-up batch and 5000 operations, the single run one batch of each.
    timed = {"operations": 5000, "allocations": 9000 + 3 * (5000 + 1000)}
    single = {"operations": 1000, "allocations": 9000 + 3 * (1000 + 1000)}
    assert PERF.allocations_per_operation(timed, single) == pytest.approx(3.0)
    assert PERF.allocations_per_operation(single, single) == 0.0


def test_allocations_per_operation_use_all_workers_in_the_reference():
    batch, workers, warmup, cost = 1000, 4, 1000, 3

    def row(operations):
        return {"operations": operations,
                "allocations": 9000 + cost * (operations + warmup)}

    single = row(batch * workers)
    assert PERF.allocations_per_operation(row(20000), single) == pytest.approx(cost)
    assert PERF.allocations_per_operation(single, single) == 0.0
    assert PERF.allocations_per_operation(row(3000), single) == 0.0


def test_primitive_cells_report_costs_ratios_and_scaling():
    row = {
        "primitive": "instance", "cpython": 10_000_000.0, "allocations": 1.0,
        "rates": {"0/1": 20_000_000.0, "0/4": 70_000_000.0,
                  "1/1": 5_000_000.0, "1/4": 4_000_000.0},
    }
    cells = [cell.strip() for cell in PERF.primitive_cells(row, (0, 1), (1, 4))]
    # 100 ns in CPython, 50 ns compiled (half), one object per operation;
    # the second collector costs four times the first; the first scales
    # 3.5x to four workers and the second falls to 0.8x.
    assert cells == ["instance", "100.0", "50.0", "0.50", "1.0", "4.00", "3.50", "0.80"]


def test_primitive_changes_star_only_agreed_moves(capsys):
    row = {"primitive": "dict", "changes": {"1/1": 12.0, "1/4": 12.0, "2/1": 1.0},
           "agreed": {"1/1": True, "1/4": False, "2/1": True}}
    PERF.print_primitive_change(row, (1,), (1, 4), first=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["primitive", "gc1w1", "gc1w4"]
    assert lines[1].split() == ["dict", "+12.0%*", "+12.0%"]


def test_an_arm_takes_head_and_only_the_named_working_tree_paths(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    (repository / "pcc/runtime").mkdir(parents=True)
    (repository / "scripts").mkdir()
    (repository / "docs").mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True)

    def write(relative, text):
        (repository / relative).write_text(text)

    git("init", "-q")
    git("config", "user.email", "perf@example.invalid")
    git("config", "user.name", "perf")
    write("pcc/runtime/mine.py", "committed\n")
    write("pcc/runtime/gone.py", "committed\n")
    write("pcc/other.py", "committed\n")
    write("scripts/tool.py", "committed\n")
    write("docs/notes.md", "outside the sealed set\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    write("pcc/runtime/mine.py", "my edit\n")          # named by --with
    write("pcc/runtime/new.py", "my new file\n")        # named, untracked
    (repository / "pcc/runtime/gone.py").unlink()       # named, deleted
    write("pcc/other.py", "another session's edit\n")   # not named
    write("scripts/unfinished.py", "another session's file\n")

    monkeypatch.setattr(PERF, "HOME", tmp_path / "home")
    arm = PERF.seal("probe", ("pcc/runtime",), root=repository, gc2_pacer_percent=100)
    source = arm / "source"
    assert (source / "pcc/runtime/mine.py").read_text() == "my edit\n"
    assert (source / "pcc/runtime/new.py").read_text() == "my new file\n"
    assert not (source / "pcc/runtime/gone.py").exists()
    assert (source / "pcc/other.py").read_text() == "committed\n"
    assert (source / "scripts/tool.py").read_text() == "committed\n"
    assert not (source / "scripts/unfinished.py").exists()
    assert not (source / "docs").exists()
    identity = json.loads((arm / "source.json").read_text())
    assert identity["gc2_pacer_percent"] == 100
    assert identity["gc2_exact_list_bridge"] is False
    exact = PERF.seal("exact", root=repository, gc2_exact_list_bridge=True)
    exact_identity = json.loads((exact / "source.json").read_text())
    assert exact_identity["revision"] == identity["revision"]
    assert exact_identity["gc2_exact_list_bridge"] is True
    assert exact_identity["gc2_pacer_percent"] is None
    assert identity["overlaid"] == [
        "pcc/runtime/gone.py", "pcc/runtime/mine.py", "pcc/runtime/new.py"]
    assert sorted(identity["left_out"]) == ["pcc/other.py", "scripts/unfinished.py"]
    assert set(identity["files"]) == {
        "pcc/runtime/mine.py", "pcc/runtime/new.py", "pcc/other.py", "scripts/tool.py"}
    assert b"my edit" in (arm / "worktree.diff").read_bytes()
    assert b"another session" not in (arm / "worktree.diff").read_bytes()
    with pytest.raises(SystemExit):
        PERF.seal("probe", (), root=repository)


@pytest.mark.parametrize("platform,flag,cap,reserve", [
    ("darwin", "--darwin-preflight-reserve-bytes", "4294967296", "536870912"),
    ("linux", "--linux-physical-preflight-reserve-bytes", "1073741824", "7516192768"),
    ("freebsd", "--darwin-preflight-reserve-bytes", "1073741824", "7516192768"),
])
def test_arm_passes_its_preflight_reserve_and_reports_a_refused_start(
        tmp_path, monkeypatch, capsys, platform, flag, cap, reserve):
    monkeypatch.setattr(PERF.sys, "platform", platform)
    monkeypatch.setattr(PERF, "HOME", tmp_path / "home")
    sealed = tmp_path / "home/arms/probe"
    (sealed / "source/benchmarks/handler").mkdir(parents=True)
    (sealed / "source.json").write_text(json.dumps(
        {"revision": "0" * 40, "overlaid": [], "left_out": []}))
    monkeypatch.setattr(PERF, "seal", lambda name, paths: sealed)
    monkeypatch.setattr(PERF, "_gateway_package", lambda: tmp_path / "gateway")
    (tmp_path / "gateway").mkdir()
    monkeypatch.setattr(PERF, "performance_lock", lambda: __import__("contextlib").nullcontext())
    seen = []
    from scripts import run_process_tree_sample
    monkeypatch.setattr(run_process_tree_sample, "main", lambda argv: seen.append(argv) or 2)
    monkeypatch.setattr(PERF.os, "environ", dict(PERF.os.environ))
    assert PERF.main(["arm", "probe", "--max-tree-rss", cap,
                      "--preflight-reserve", reserve]) == 1
    argv = seen[0]
    assert argv[argv.index("--max-tree-rss-bytes") + 1] == cap
    assert argv[argv.index("--timeout") + 1] == "900"
    assert argv[argv.index(flag) + 1] == reserve
    assert [option for option in argv if option.endswith("preflight-reserve-bytes")] == [flag]
    assert "--auto-tree-rss-ceiling-bytes" not in argv
    assert argv[-2:] == ["_build", str(sealed)]
    assert "build did not start (guard exit 2)" in capsys.readouterr().out
    seen.clear()
    PERF.main(["arm", "probe"])
    assert seen[0][seen[0].index(flag) + 1] == "8589934592"
    assert seen[0][seen[0].index("--max-tree-rss-bytes") + 1] == "6442450944"


@pytest.mark.parametrize("mode", ["failed", "missing", "invalid", "empty", "valid"])
def test_profile_requires_a_fresh_successful_sampler_report(monkeypatch, tmp_path, mode):
    target = tmp_path / "gc3w4loop.sample"
    previous = "Call graph:\n    8 previous_function\nTotal number in stack\n"
    target.write_text(previous)
    binary = tmp_path / "benchmark"
    fresh = "Call graph:\n    4 current_function\nTotal number in stack\n"
    writes = {"failed": fresh, "invalid": "incomplete", "empty": "Call graph:\n",
              "valid": fresh}
    waited = []

    class Process:
        pid = 123
        returncode = 0

        def communicate(self, *, timeout):
            waited.append(timeout)

    def sample(argv, **kwargs):
        output = Path(argv[-1])
        if mode in writes:
            output.write_text(writes[mode])
        return subprocess.CompletedProcess(argv, int(mode == "failed"))

    monkeypatch.setattr(PERF, "binary_of", lambda name: binary)
    monkeypatch.setattr(PERF, "arm_dir", lambda name: tmp_path)
    monkeypatch.setattr(PERF, "run_primitive", lambda *args: {"rate": 1000})
    monkeypatch.setattr(PERF.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(PERF.subprocess, "run", sample)
    monkeypatch.setattr(PERF.time, "sleep", lambda _: None)
    monkeypatch.setattr(PERF, "text_symbols", lambda path: [])

    if mode == "valid":
        assert PERF.profile("base", 3, 4, 1, "loop") == (
            [[4, 4, "current_function"]], 4_000_000.0, "operation")
        assert target.read_text() == fresh
    else:
        with pytest.raises(SystemExit, match="sample"):
            PERF.profile("base", 3, 4, 1, "loop")
        assert target.read_text() == previous
    assert waited == [900]
    assert sorted(tmp_path.iterdir()) == [target]

def test_gc2_pacer_environment_is_sealed_and_checks_build_attestation(tmp_path, monkeypatch):
    monkeypatch.setattr(PERF, "HOME", tmp_path)
    monkeypatch.setenv("PCC_GC2_PACER_PERCENT", "777")
    arm = tmp_path / "arms/test"
    arm.mkdir(parents=True)
    binary = arm / "native_stats"
    binary.write_bytes(b"owner")
    identity = arm / "source.json"
    report = arm / "build.json"
    assert PERF._sealed_runtime_env(binary, 2) == {
        "PATH": "/nonexistent", "PCC_GC_BACKEND": "2",
    }
    identity.write_text(json.dumps({"revision": "rev", "gc2_pacer_percent": 100}))
    with pytest.raises(SystemExit, match="no matching GC2 pacer attestation"):
        PERF._sealed_runtime_env(binary, 2)
    report.write_text(json.dumps({
        "complete": True, "revision": "rev", "gc2_pacer_percent": 100,
        "gc2_pacer_attested": {"on": 100, "off": -1, "gc0": -1},
        "programs": {"native_stats": {"sha256": PERF.hashlib.sha256(b"owner").hexdigest()}},
    }))
    assert PERF.binary_of("test") == binary
    assert PERF._sealed_runtime_env(binary, 2)["PCC_GC2_PACER_PERCENT"] == "100"
    for other in (0, 1, 3, 4):
        assert "PCC_GC2_PACER_PERCENT" not in PERF._sealed_runtime_env(binary, other)
    binary.write_bytes(b"unsealed")
    with pytest.raises(SystemExit, match="binary does not match"):
        PERF.binary_of("test")
    identity.write_text(json.dumps({"revision": "rev"}))
    with pytest.raises(SystemExit, match="no matching GC2 pacer attestation"):
        PERF._sealed_runtime_env(binary, 2)


def test_gc2_attestation_requires_enabled_stock_and_other_collector(tmp_path, monkeypatch):
    (tmp_path / "programs").mkdir()
    built = []
    seen = []

    def compiler(source, binary, **kwargs):
        built.append((Path(source).read_text(), Path(binary), kwargs))

    def run(argv, *, env, **kwargs):
        seen.append(env)
        raw = env.get("PCC_GC2_PACER_PERCENT", "-1")
        return subprocess.CompletedProcess(argv, 0, f"{raw}\n", "")

    monkeypatch.setattr(PERF.subprocess, "run", run)
    assert PERF._attest_gc2_pacer(tmp_path, tmp_path / "runtime.a", 0, compiler) == {
        "on": 0, "off": -1, "gc0": -1,
    }
    assert "pcc_gc2_pacer_mode" in built[0][0]
    assert [(item["PCC_GC_BACKEND"], item.get("PCC_GC2_PACER_PERCENT"))
            for item in seen] == [("2", "0"), ("2", None), ("0", None)]
    monkeypatch.setattr(PERF.subprocess, "run",
                        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "-1\n", ""))
    with pytest.raises(SystemExit, match="attestation on failed"):
        PERF._attest_gc2_pacer(tmp_path, tmp_path / "runtime.a", 100, compiler)


def test_comparison_records_sealed_mode_only_on_gc2_rows(tmp_path, monkeypatch):
    for name, percent in (("base", None), ("candidate", 100)):
        arm = tmp_path / name
        arm.mkdir()
        (arm / "source.json").write_text(json.dumps({"gc2_pacer_percent": percent}))
    monkeypatch.setattr(PERF, "binary_of", lambda name: tmp_path / name / "native_stats")
    monkeypatch.setattr(PERF, "run_once", lambda *args: _run(100))
    rows = PERF.compare("base", "candidate", 1, (0, 2), (1,), 10)
    assert "gc2_pacer_percent_by_arm" not in rows[0]
    assert rows[1]["gc2_pacer_percent_by_arm"] == {"base": None, "candidate": 100}


def test_handler_receipts_keep_peak_pause_memory_and_p99_per_pair(tmp_path, monkeypatch):
    process_args = []
    payload = {
        "requests": 100, "started_at": 1.0, "finished_at": 2.0, "gc": {"120": 81, "121": 42},
        "latencies_ms": list(reversed(range(100))),
    }

    class Process:
        _pcc_usage = 0.05
        _pcc_peak_rss_bytes = 4096
        returncode = 0

        def __init__(self, argv, **kwargs):
            process_args.append(argv)

        def communicate(self, timeout):
            return json.dumps(payload), ""

    monkeypatch.setattr(PERF, "MeasuredPopen", Process)
    results = PERF.run_once(tmp_path / "native_stats", 2, 4, 1, latency_receipts=True)
    assert process_args[0][-2:] == ["--samples", "4"]
    assert results["latency_p99_ms"] == 98
    assert PERF._rss_bytes(2, "linux") == 2048
    assert PERF._rss_bytes(2048, "darwin") == 2048
    assert PERF._rss_bytes(2, "freebsd") is None
    second = dict(results, max_stw_us=55, peak_rss_bytes=12288, latency_p99_ms=102)
    receipt = PERF.summarize_pairs([results, second], [second, results], [results, results])
    assert receipt["run_receipts"]["a"] == [
        {"qps": 100, "max_stw_us": 42, "peak_rss_bytes": 4096, "latency_p99_ms": 98},
        {"qps": 100, "max_stw_us": 55, "peak_rss_bytes": 12288, "latency_p99_ms": 102},
    ]
    assert receipt["run_receipts"]["b"] == list(reversed(receipt["run_receipts"]["a"]))
    assert len(receipt["run_receipts"]["control"]) == 2
    payload["gc"].pop("121")
    with pytest.raises(SystemExit, match="missing required"):
        PERF.run_once(tmp_path / "native_stats", 2, 4, 1, latency_receipts=True)


@pytest.mark.parametrize("samples", [[], [float("nan")], [-1], [True]])
def test_handler_p99_refuses_invalid_timed_sample_sets(samples):
    with pytest.raises(SystemExit, match="latency receipt missing"):
        PERF._handler_p99_ms({"requests": 1, "latencies_ms": samples})


def test_exact_list_mode_rejects_missing_drifted_and_changed_build_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(PERF, "HOME", tmp_path)
    monkeypatch.setenv("PCC_GC2_EXACT_LIST_BRIDGE", "1")
    arm = tmp_path / "arms/test"
    arm.mkdir(parents=True)
    binary = arm / "native_stats"
    binary.write_bytes(b"owned")
    identity = arm / "source.json"
    report = arm / "build.json"
    assert "PCC_GC2_EXACT_LIST_BRIDGE" not in PERF._sealed_runtime_env(binary, 2)
    metadata = {"revision": "rev", "gc2_exact_list_bridge": True}
    identity.write_text(json.dumps(metadata))
    with pytest.raises(SystemExit, match="no matching GC2 exact-list attestation"):
        PERF.binary_of("test")
    built = {
        **metadata, "complete": True,
        "gc2_exact_list_attested": {"on": 2, "off": 0, "gc0": 0},
        "programs": {"native_stats": {"sha256": PERF.hashlib.sha256(b"owned").hexdigest()}},
    }
    report.write_text(json.dumps(built))
    assert PERF.binary_of("test") == binary
    assert PERF._sealed_runtime_env(binary, 2)["PCC_GC2_EXACT_LIST_BRIDGE"] == "1"
    for backend in (0, 1, 3, 4):
        assert "PCC_GC2_EXACT_LIST_BRIDGE" not in PERF._sealed_runtime_env(binary, backend)
    for changed in ({"gc2_exact_list_bridge": False}, {"revision": "different"},
                    {"gc2_exact_list_attested": {"on": 2, "off": 0, "gc0": 2}}):
        report.write_text(json.dumps({**built, **changed}))
        with pytest.raises(SystemExit, match="no matching GC2 exact-list attestation"):
            PERF.binary_of("test")
    report.write_text(json.dumps(built))
    binary.write_bytes(b"different file")
    with pytest.raises(SystemExit, match="binary does not match"):
        PERF.binary_of("test")
    with pytest.raises(SystemExit, match="separate arms"):
        PERF.seal("bad", gc2_exact_list_bridge=True, gc2_pacer_percent=100)
    with pytest.raises(SystemExit, match="must be a boolean"):
        PERF._gc2_exact_list_bridge({"gc2_exact_list_bridge": 1})


def test_exact_list_attestation_deliberately_poisons_gc0_env(tmp_path, monkeypatch):
    (tmp_path / "programs").mkdir()
    compiled = []
    observed = []

    def compiler(source, binary, **kwargs):
        compiled.append(Path(source).read_text())
        assert kwargs["runtime_archive"] == str(tmp_path / "runtime.a")

    def run(argv, *, env, **kwargs):
        raw = env.get("PCC_GC2_EXACT_LIST_BRIDGE")
        backend = env["PCC_GC_BACKEND"]
        observed.append((backend, raw))
        value = 2 if backend == "2" and raw == "1" else 0
        return subprocess.CompletedProcess(argv, 0, f"{value}\n", "")

    monkeypatch.setattr(PERF.subprocess, "run", run)
    assert PERF._attest_gc2_exact_list_bridge(tmp_path, tmp_path / "runtime.a", compiler) == {
        "on": 2, "off": 0, "gc0": 0,
    }
    assert observed == [("2", "1"), ("2", None), ("0", "1")]
    assert "pcc_gc2_exact_list_bridge_mode" in compiled[0]
    monkeypatch.setattr(PERF.subprocess, "run", lambda argv, *, env, **kwargs:
                        subprocess.CompletedProcess(
                            argv, 0, "2\n" if env.get("PCC_GC2_EXACT_LIST_BRIDGE") else "0\n", "",
                        ))
    with pytest.raises(SystemExit, match="attestation gc0 failed"):
        PERF._attest_gc2_exact_list_bridge(tmp_path, tmp_path / "runtime.a", compiler)


def test_comparison_records_exact_list_selector_only_for_gc2(tmp_path, monkeypatch):
    for name, bridge in (("base", False), ("candidate", True)):
        arm = tmp_path / name
        arm.mkdir()
        (arm / "source.json").write_text(json.dumps({"gc2_exact_list_bridge": bridge}))
    monkeypatch.setattr(PERF, "binary_of", lambda name: tmp_path / name / "native_stats")
    monkeypatch.setattr(PERF, "run_once", lambda *args: _run(100))
    rows = PERF.compare("base", "candidate", 1, (0, 2), (1,), 10)
    assert "gc2_exact_list_bridge_by_arm" not in rows[0]
    assert rows[1]["gc2_exact_list_bridge_by_arm"] == {"base": False, "candidate": True}

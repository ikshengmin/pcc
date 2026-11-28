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


def test_a_function_self_time_is_split_by_its_callers():
    rows = PERF.parse_sample(SAMPLE, SYMBOLS, "bench")
    assert PERF.function_callers(rows, WAIT) == {"pcc_thread_graph_lock_acquire_for": 25}
    assert PERF.function_callers(rows, WAIT, 2) == {
        "pcc_thread_graph_lock_acquire_for < work": 25}
    # Only self samples count: work's time under its callees is theirs.
    assert PERF.function_callers(rows, "work") == {"main": 25}
    assert PERF.function_callers(rows, "absent") == {}


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


def test_a_duplicate_base_arm_gives_each_row_its_own_noise_floor():
    row = PERF.summarize_pairs(
        [_run(100)] * 5, [_run(q) for q in (106, 107, 105, 108, 106)],
        control=[_run(q) for q in (102, 99, 101, 98, 100)])
    row.update({"gc": 3, "workers": 1})
    PERF.apply_control([row])
    assert row["verdict"] == "gain"
    assert row["noise_percent"] == pytest.approx(2.0)


def _controlled_row(first, second, control):
    row = PERF.summarize_pairs([_run(q) for q in first], [_run(q) for q in second],
                               control=[_run(q) for q in control])
    row.update({"gc": 0, "workers": 4})
    return row


def test_a_change_inside_a_noise_floor_of_five_percent_or_more_is_inconclusive():
    noisy = [118, 86, 104, 97, 101]
    rows = [
        _controlled_row([100] * 5, [104, 103, 106, 102, 104], noisy),
        _controlled_row([100] * 5, [125, 122, 121, 124, 123], noisy),
    ]
    PERF.apply_control(rows)
    assert [row["verdict"] for row in rows] == ["inconclusive", "gain"]
    assert rows[0]["noise_percent"] == pytest.approx(18.0)


def test_fewer_than_five_pairs_stay_a_screen_whatever_the_control_says():
    rows = [_controlled_row([100] * 3, [150] * 3, [100] * 3)]
    PERF.apply_control(rows)
    assert rows[0]["verdict"] == "screen"


def test_without_a_control_a_comparison_cannot_count(capsys):
    rows = [_row(3, 1, [100] * 5, [110] * 5)]
    PERF.apply_control(rows)
    PERF.print_comparison(rows)
    assert rows[0]["verdict"] == "no control"
    assert "cannot count toward the freeze rule" in capsys.readouterr().out


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


@pytest.mark.parametrize("workers", PERF.WORKERS)
def test_the_asyncio_counterpart_splits_the_requests_over_worker_processes(workers):
    run = PERF.asyncio_run(workers, 2)
    assert run["requests"] == 200
    assert run["qps"] > 0


def test_asyncio_is_recorded_and_compared_per_worker_count(tmp_path, monkeypatch):
    monkeypatch.setattr(PERF, "HOME", tmp_path)
    assert PERF.recorded_asyncio_rates() == {}
    # A record from before worker counts holds one event loop's rate: it
    # is the counterpart of one worker only, never of four.
    (tmp_path / "asyncio.json").write_text(json.dumps({"qps": 87000.0}))
    assert PERF.recorded_asyncio_rates() == {1: 87000.0}
    rates = {1: [10.0, 30.0, 20.0], 4: [40.0, 60.0, 50.0]}
    monkeypatch.setattr(
        PERF, "asyncio_run",
        lambda workers, rounds: {"qps": rates[workers].pop(0), "requests": rounds * 100})
    assert PERF.measure_asyncio(100, 3) == {1: 20.0, 4: 50.0}
    assert PERF.recorded_asyncio_rates() == {1: 20.0, 4: 50.0}
    assert json.loads((tmp_path / "asyncio.json").read_text())["qps"] == 20.0


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


def test_frontend_cache_identity_follows_the_compiler_not_the_runtime():
    # An arm that changes code generation must not reuse another arm's
    # frontend IR; arms that differ only in the runtime's sources share it.
    files = {"pcc/frontends/python/codegen/a.py": "1" * 64,
             "pcc/backend/b.py": "2" * 64,
             "pcc/runtime/py/py_list.py": "3" * 64,
             "tests/python/test_x.py": "4" * 64}
    base = PERF.frontend_cache_identity(files)
    assert base == PERF.frontend_cache_identity(dict(files))
    changed_codegen = dict(files, **{"pcc/frontends/python/codegen/a.py": "5" * 64})
    assert PERF.frontend_cache_identity(changed_codegen) != base
    added_frontend = dict(files, **{"pcc/frontends/python/new.py": "6" * 64})
    assert PERF.frontend_cache_identity(added_frontend) != base
    changed_runtime = dict(files, **{"pcc/runtime/py/py_list.py": "7" * 64})
    assert PERF.frontend_cache_identity(changed_runtime) == base
    changed_test = dict(files, **{"tests/python/test_x.py": "8" * 64})
    assert PERF.frontend_cache_identity(changed_test) == base


def test_counted_runs_parse_darwin_and_linux_counters():
    darwin = ("        0.31 real         0.25 user         0.02 sys\n"
              "             9962257  instructions retired\n"
              "             6056643  cycles elapsed\n")
    assert PERF.parse_counted_run(darwin, True) == (9962257, 6056643)
    linux = "12345678,,instructions:u,1000,100.00,,\n2345678,,cycles:u,1000,100.00,,\n"
    assert PERF.parse_counted_run(linux, False) == (12345678, 2345678)
    with pytest.raises(SystemExit):
        PERF.parse_counted_run("no counters here", True)


def test_instruction_rows_take_the_difference_of_two_run_lengths():
    # 10 and 50 rounds of 20 requests: 800 requests apart, start-up cancels.
    assert PERF.per_request((1_000_000, 2_000_000), (1_800_000, 3_600_000), 10, 50) == (1000.0, 2000.0)
    rows = PERF.instruction_rows({
        "base": [(1000.0, 2000.0), (1002.0, 2100.0), (998.0, 1900.0)],
        "new": [(990.0, 1800.0), (989.0, 1850.0), (991.0, 1750.0)],
    })
    assert rows[0]["instructions"] == 1000.0 and "instruction_change" not in rows[0]
    assert rows[0]["instruction_spread"] == pytest.approx(0.4)
    assert rows[0]["cycle_spread"] == pytest.approx(10.0)
    assert rows[0]["cycle_runs"] == [2000, 2100, 1900]
    assert rows[1]["instruction_change"] == pytest.approx(-1.0)
    assert rows[1]["cycle_change"] == pytest.approx(-10.0)


_WAIT_THEN_SPIN = """
import os, sys, time

def waiting():
    time.sleep(0.4)

def spinning():
    end = time.process_time() + 0.4
    total = 0
    while time.process_time() < end:
        total += 1
    return total

waiting()
spinning()
if sys.argv[1:] == ["hard-exit"]:
    os._exit(0)
"""


def _profiled_run(tmp_path, *argv):
    inject = tmp_path / "inject"
    samples = tmp_path / "samples"
    inject.mkdir()
    samples.mkdir()
    (inject / "sitecustomize.py").write_text(PERF._COMPILE_SAMPLER)
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(inject),
           "PCC_COMPILE_PROFILE_DIR": str(samples), "PCC_COMPILE_PROFILE_HZ": "1000"}
    subprocess.run([sys.executable, "-c", _WAIT_THEN_SPIN, *argv], env=env,
                   check=True, timeout=60)
    return PERF.read_compile_samples(samples)


@pytest.mark.parametrize("argv", ((), ("hard-exit",)))
def test_compile_sampler_counts_processor_time_not_waiting(tmp_path, argv):
    # A wall-clock sampler charges the sleep to `waiting`; CPU-time samples
    # land in `spinning` only, and os._exit still writes the samples.
    processes = _profiled_run(tmp_path, *argv)
    assert len(processes) == 1 and processes[0]["cpu"] >= 0.3
    rows = PERF.compile_profile_rows(processes)
    self_time = dict(rows["self"])
    spinning = sum(count for frame, count in self_time.items() if frame.startswith("spinning "))
    waiting = sum(count for frame, count in self_time.items() if frame.startswith("waiting "))
    assert spinning >= 0.7 * rows["samples"] and spinning > 100
    assert waiting <= 0.02 * rows["samples"]


def test_compile_profile_rows_group_roles_owners_and_inclusive_time():
    processes = [
        {"cpu": 2.0, "argv": "/x/pcc_perf.py _compile /arm native_stats /run",
         "folded": {"main [pcc/driver/cli.py];emit [pcc/backend/self_backend.py]": 30,
                    "main [pcc/driver/cli.py];_find_and_load [<frozen importlib._bootstrap>]": 10}},
        {"cpu": 5.0, "argv": "-m pcc --pcc-python-multi-codegen-worker /tmp/w",
         "folded": {"run [pcc/frontends/python/worker.py];_local_names [pcc/frontends/python/opt/mem2reg.py]": 50,
                    "run [pcc/frontends/python/worker.py];communicate [python/subprocess.py]": 10}},
        {"cpu": 1.0, "argv": "-m pcc --pcc-python-multi-codegen-worker /tmp/v", "folded": {}},
    ]
    rows = PERF.compile_profile_rows(processes)
    assert rows["samples"] == 100
    assert rows["roles"] == [["pcc-python-multi-codegen-worker", 2, 6.0],
                             ["build coordinator", 1, 2.0]]
    assert dict(rows["owners"]) == {
        "pcc/frontends/python": 50, "pcc/backend": 30,
        "CPython import": 10, "CPython stdlib/other": 10,
    }
    assert rows["self"][0] == ("_local_names [pcc/frontends/python/opt/mem2reg.py]", 50)
    inclusive = dict(rows["inclusive"])
    assert inclusive["run [pcc/frontends/python/worker.py]"] == 60
    assert inclusive["main [pcc/driver/cli.py]"] == 40


def test_ir_shapes_rank_functions_and_repeated_blocks():
    release = ("x.owned.release.{n}:\n  store i1 0, ptr %f\n"
               "  call void (ptr, ptr) @pcc_gc_store_root(ptr %s, ptr null)\n  br label %c{n}\n")
    body = "".join(release.format(n=n) for n in range(30))
    ir = ("define ptr @big(ptr %a) {\nentry:\n  ret ptr %a\n" + body + "}\n"
          "define i64 @small() {\nentry:\n  %v = add i64 1, 2\n  ret i64 %v\n}\n")
    rows = PERF.ir_shape_rows(ir.splitlines(keepends=True))
    assert [function[0] for function in rows["functions"]] == ["big", "small"]
    assert rows["functions"][0][2] == 31
    assert rows["functions"][0][3].most_common(1)[0][0] == "x.owned.release.#"
    shape, (count, lines, families, owners) = rows["shapes"][0]
    assert shape == ("store", "call pcc_gc_store_root", "br") and count == 30 and lines == 120
    assert owners.most_common(1)[0][0] == "big"


def test_linked_worktree_uses_the_main_checkout_environment(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--detach", str(linked)],
                   check=True)
    original = PERF.REPO_ROOT
    try:
        PERF.REPO_ROOT = linked
        assert PERF.main_checkout().resolve() == main.resolve()
        assert PERF.project_environment().resolve() == (main / ".venv").resolve()
        PERF.REPO_ROOT = main
        assert PERF.main_checkout().resolve() == main.resolve()
    finally:
        PERF.REPO_ROOT = original


def test_callgrind_reads_full_ir_without_summing_inclusive_calls():
    # Callgrind's summary is whole-run cost; totals covers represented
    # self costs and may be smaller. The call edge contains inclusive cost.
    profile = """# callgrind format
version: 1
part: 1
positions: line
events: Dr Ir Dw
summary: 40 1820000 20
fn=(1) caller
2 2 100 1
cfn=(2) callee
calls=1 5
2 20 800 3
fn=(2)
5 10 500 9
totals: 12 600 10
"""
    assert PERF.parse_callgrind_counted_run(profile) == (1820000, None)
    assert PERF.parse_callgrind_counted_run(iter(profile.splitlines())) == (1820000, None)


@pytest.mark.parametrize("profile", [
    "events: Dr\nsummary: 1\n",
    "events: Ir Ir\nsummary: 1 2\n",
    "events: Ir\n",
    "events: Ir\nsummary: <not counted>\n",
    "events: Ir\nsummary: -1\n",
    "events: Ir\nsummary: 0\n",
    "events: Dr Ir\nsummary: 100\n",
    "events: Ir\nsummary: 1\ntotals: 2\n",
    "events: Ir\nsummary: 1\nsummary: 1\n",
    "events: Ir\nsummary: 1\ntotals: 1\ntotals: 1\n",
    "part: 1\nevents: Ir\nsummary: 1\npart: 2\nevents: Ir\nsummary: 2\n",
])
def test_callgrind_invalid_or_ambiguous_counts_fail_closed(profile):
    with pytest.raises(SystemExit, match="Callgrind"):
        PERF.parse_callgrind_counted_run(profile)


def test_callgrind_instruction_difference_never_estimates_cycles_or_ipc(capsys):
    first = PERF.per_request((1_000_000, None), (1_800_000, None), 10, 50)
    second = PERF.per_request((1_000_000, None), (1_792_000, None), 10, 50)
    assert first == (1000.0, None) and second == (990.0, None)
    rows = PERF.instruction_rows({"base": [first], "next": [second]}, counter_backend="callgrind")
    assert rows[0]["instructions"] == 1000
    assert rows[0]["cycles"] is None and rows[0]["ipc"] is None
    assert rows[0]["cycle_runs"] is None and rows[0]["cycle_spread"] is None
    assert rows[1]["instruction_change"] == pytest.approx(-1)
    assert "cycle_change" not in rows[1]
    PERF.print_instructions(rows)
    text = capsys.readouterr().out
    assert "Callgrind Ir" in text and "cycles unavailable" in text and "IPC unavailable" in text
    with pytest.raises(SystemExit, match="counter"):
        PERF.per_request((1_000_000, None), (1_800_000, 3), 10, 50)
    with pytest.raises(SystemExit, match="counter"):
        PERF.per_request((1_800_000, None), (1_000_000, None), 10, 50)


def test_callgrind_uses_one_all_thread_profile_and_exact_completed_work(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(PERF.sys, "platform", "linux")
    monkeypatch.setattr(PERF.shutil, "which", lambda name: "/test/valgrind" if name == "valgrind" else None)
    monkeypatch.setattr(PERF, "_sealed_runtime_env", lambda binary, collector: {"PCC_GC_BACKEND": "0"})
    flags = []
    summary = {"requests": 200, "concurrency": 20, "rounds": 10, "warmup_requests": 40}
    create_second = False
    def fake_run(command, **kwargs):
        flags[:] = command
        path = next(x.partition("=")[2] for x in command if x.startswith("--callgrind-out-file="))
        assert "%p" in path
        Path(path.replace("%p", "1234")).write_text("events: Ir\nsummary: 1800000\ntotals: 1800000\n")
        if create_second:
            Path(path.replace("%p", "4321")).write_text("events: Ir\nsummary: 1\n")
        return SimpleNamespace(returncode=0, stdout=json.dumps(summary), stderr="valgrind diagnostic")
    monkeypatch.setattr(PERF.subprocess, "run", fake_run)
    binary = tmp_path / "native_stats"
    assert PERF.counted_run(binary, 10, 1, counter_backend="callgrind") == (1800000, None)
    assert flags[0] == "/test/valgrind"
    assert {"--tool=callgrind", "--separate-threads=no", "--trace-children=no",
            "--collect-atstart=yes", "--instr-atstart=yes", "--cache-sim=no",
            "--branch-sim=no"} <= set(flags)
    assert flags[-6:] == [str(binary), "20", "0", "10", "--summary", "1"]
    assert not Path(next(x.partition("=")[2] for x in flags if x.startswith("--callgrind-out-file="))).parent.exists()
    summary["requests"] = 199
    with pytest.raises(SystemExit, match="handler.*requests"):
        PERF.counted_run(binary, 10, 1, counter_backend="callgrind")
    summary["requests"] = 200
    summary["warmup_requests"] = 20
    with pytest.raises(SystemExit, match="handler.*requests"):
        PERF.counted_run(binary, 10, 1, counter_backend="callgrind")
    summary["warmup_requests"] = 40
    create_second = True
    with pytest.raises(SystemExit, match="Callgrind.*one"):
        PERF.counted_run(binary, 10, 1, counter_backend="callgrind")


def test_instruction_counter_backend_availability_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(PERF.sys, "platform", "linux")
    monkeypatch.setattr(PERF.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="valgrind"):
        PERF.counted_run(tmp_path / "native_stats", 10, 1, counter_backend="callgrind")
    with pytest.raises(SystemExit, match="perf"):
        PERF.counted_run(tmp_path / "native_stats", 10, 1)
    monkeypatch.setattr(PERF.sys, "platform", "darwin")
    with pytest.raises(SystemExit, match="Linux"):
        PERF.counted_run(tmp_path / "native_stats", 10, 1, counter_backend="callgrind")


def test_instr_cli_explicit_callgrind_and_hardware_defaults(monkeypatch, capsys):
    monkeypatch.setattr(PERF.sys, "platform", "linux")
    monkeypatch.setattr(PERF.shutil, "which", lambda name: "/test/" + name)
    calls = []
    def fake_measure(names, repeats, short_rounds, long_rounds, workers, *, counter_backend):
        calls.append((names, repeats, short_rounds, long_rounds, workers, counter_backend))
        cycles = None if counter_backend == "callgrind" else 2_000.0
        return {name: [(1_000.0, cycles)] for name in names}
    monkeypatch.setattr(PERF, "measure_instructions", fake_measure)
    assert PERF.main(["instr", "base", "control", "--counter-backend", "callgrind",
                      "--repeats", "5"]) == 0
    assert calls[-1] == (["base", "control"], 5, 10, 50, 1, "callgrind")
    text = capsys.readouterr().out
    assert "Callgrind Ir" in text and "cycles unavailable" in text
    assert PERF.main(["instr", "base"]) == 0
    assert calls[-1][-1] == "perf"
    assert "cycles per request" in capsys.readouterr().out
    monkeypatch.setattr(PERF.sys, "platform", "darwin")
    assert PERF.main(["instr", "base"]) == 0
    assert calls[-1][-1] == "darwin"
    assert "cycles per request" in capsys.readouterr().out


def test_callgrind_function_self_cost_compressed_ids_and_inclusive_edges():
    profile = """# callgrind format
version: 1
part: 1
positions: instr line
events: Dr Ir Dw
summary: 24 1000 15
ob=(1) /bin/native_stats
fl=(1) source.py
fn=(1) main
0x100 5 1 100 1
cob=(2) /lib/libc.so
cfn=(2) work
calls=4 0x200 8
* * 20 800 14
fi=(3) inline.py
+4 +1 2 20
fe=(1)
ob=(2)
fn=(2)
0x200 8 10 580 5
cfn=(1)
calls=1 0x100 5
* * 1 100 1
ob=(1)
fn=(3) ???
0x400 0 3 200 1
totals: 16 900 7
"""
    total, costs = PERF.parse_callgrind_function_profile(iter(profile.splitlines()))
    assert total == 1000
    assert costs == {
        ("/bin/native_stats", "main"): 120,
        ("/lib/libc.so", "work"): 580,
        ("/bin/native_stats", PERF.CALLGRIND_UNKNOWN_SYMBOL): 200,
        PERF.CALLGRIND_UNREPRESENTED: 100,
    }
    assert sum(costs.values()) == total


@pytest.mark.parametrize("body", [
    "fn=(9)\n1 1\n",
    "fn=(1) first\nfn=(1) different\n1 1\n",
    "fn=first\ncalls=1 2\n",
    "fn=first\ncalls=1 2\nfn=second\n1 1\n",
    "fn=first\n1 1 2\n",
    "fn=first\n1 -1\n",
    "fn=first\ninvalid 1\n",
    "fn=first\n1 2\n",
    "fn=first\n1 1\ntotals: 0\n",
    "fn=first\n1 1\nunknown=ambiguous\n",
])
def test_callgrind_function_profile_rejects_ambiguous_self_cost(body):
    with pytest.raises(SystemExit, match="Callgrind"):
        PERF.parse_callgrind_function_profile("events: Ir\nsummary: 1\n" + body)


def test_callgrind_missing_symbol_and_unrepresented_cost_are_explicit():
    total, costs = PERF.parse_callgrind_function_profile(
        "events: Ir\nsummary: 10\n1 3\n")
    assert total == 10
    assert costs == {
        (PERF.CALLGRIND_UNKNOWN_OBJECT, PERF.CALLGRIND_UNKNOWN_SYMBOL): 3,
        PERF.CALLGRIND_UNREPRESENTED: 7,
    }


def test_callgrind_function_differential_reconciles_and_keeps_negative_cost(capsys):
    main = ("native_stats", "main")
    release = ("native_stats", "release")
    unknown = ("native_stats", PERF.CALLGRIND_UNKNOWN_SYMBOL)
    gap = PERF.CALLGRIND_UNREPRESENTED
    short = {main: 1000, release: 2000, unknown: 300, gap: 700}
    long = {main: 9000, release: 1200, unknown: 700, gap: 1100}
    delta = PERF.callgrind_function_difference(short, long, 4000, 12000, 10, 50)
    assert delta == {main: 10.0, release: -1.0, unknown: 0.5, gap: 0.5}
    assert sum(delta.values()) == 10.0
    PERF.print_instruction_functions({"base": [delta], "empty": [{gap: 10.0}]}, 5)
    out = capsys.readouterr().out
    assert "self Ir/request" in out and "main" in out and "release" in out
    assert "-1.00" in out and "unknown symbol" in out and "unrepresented Ir" in out
    assert "no named function" in out
    with pytest.raises(SystemExit, match="reconcile"):
        PERF.callgrind_function_difference(short, long, 9999, 12000, 10, 50)


def test_callgrind_function_attribution_uses_same_counted_runs(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(PERF.sys, "platform", "linux")
    monkeypatch.setattr(PERF.shutil, "which", lambda name: "/test/" + name)
    monkeypatch.setattr(PERF, "_sealed_runtime_env", lambda *args: {})
    monkeypatch.setattr(PERF, "binary_of", lambda name: tmp_path / name)
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        rounds = int(command[-3])
        total = 1000 + 20 * rounds * 10
        path = next(x.partition("=")[2] for x in command if x.startswith("--callgrind-out-file="))
        Path(path.replace("%p", "1234")).write_text(
            f"events: Ir\nsummary: {total}\nob=native_stats\nfn=(1) main\n1 {total}\ntotals: {total}\n")
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "requests": 20 * rounds, "rounds": rounds, "concurrency": 20,
            "warmup_requests": 40,
        }), stderr="")
    monkeypatch.setattr(PERF.subprocess, "run", fake_run)
    assert PERF.main(["instr", "base", "--counter-backend", "callgrind",
                      "--functions", "3", "--repeats", "1"]) == 0
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "Callgrind Ir" in out and "self Ir/request" in out and "main" in out
    assert "10.00" in out
    with pytest.raises(SystemExit, match="Callgrind"):
        PERF.main(["instr", "base", "--functions", "1"])


# ---- reference runtime: a labeled LLVM experiment, not a product route ----

def _owned_runtime(tmp_path, members=("py_obj.o", "py_gen.o"), without_ir=()):
    runtime = tmp_path / "build_owned/libpy_runtime_pcc_py.a"
    objects = Path(str(runtime) + ".objects")
    objects.mkdir(parents=True)
    runtime.write_bytes(b"!<arch>\nowned")
    for member in members:
        if member not in without_ir:
            (objects / (member[:-2] + ".ll")).write_text("; runtime IR\n")
    (objects / "not_a_member.ll").write_text("; left over, not archived\n")
    Path(str(runtime) + ".provenance.json").write_text(json.dumps(
        {"members": [{"member": member, "ir_sha256": "0" * 64} for member in members]}))
    return runtime


def _recording_tools(monkeypatch):
    commands = []

    def run(argv, **kwargs):
        argv = [str(item) for item in argv]
        commands.append(argv)
        if argv[0].endswith("/ar"):
            Path(argv[2]).write_bytes(b"!<arch>\nreference")
        elif "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"object")
        return subprocess.CompletedProcess(argv, 0, "clang version 20.1.8\nTarget: x\n", "")

    monkeypatch.setattr(PERF.shutil, "which", lambda name: "/opt/bin/" + name)
    return commands, run


@pytest.mark.parametrize("kind,flags", [
    ("clang-O2", ["-O2"]),
    ("clang-codegen", ["-O2", "-Xclang", "-disable-llvm-optzns"]),
    ("clang-codegen-fastra", ["-O2", "-Xclang", "-disable-llvm-optzns",
                              "-mllvm", "-regalloc=fast"]),
])
def test_reference_runtime_compiles_each_owned_member_ir_with_clang(
        tmp_path, monkeypatch, kind, flags):
    runtime = _owned_runtime(tmp_path)
    arm = tmp_path / "arm"
    arm.mkdir()
    commands, run = _recording_tools(monkeypatch)
    archive, receipt = PERF.build_reference_runtime(
        arm, runtime, kind, "arm64-apple-darwin", run=run)
    compiles = [argv for argv in commands if "-x" in argv]
    assert sorted(Path(argv[argv.index("-o") + 1]).name for argv in compiles) == [
        "py_gen.o", "py_obj.o"]
    for argv in compiles:
        assert argv[:1 + len(flags)] == ["/opt/bin/clang", *flags]
        assert argv[argv.index("-x") + 1] == "ir"
        assert Path(argv[-3]).suffix == ".ll"
    stub = (arm / "reference-runtime/capture_gc_registers.s").read_text()
    # What the self backend's inline expansion stores, as a called function.
    assert '"_llvm.pcc.capture.gc.registers":' in stub
    for first in range(19, 31, 2):
        assert f"stp x{first}, x{first + 1}, [x0, #{(first - 19) * 8}]" in stub
    assert "stp x10, xzr, [x0, #96]" in stub and "stp xzr, xzr, [x0, #112]" in stub
    archived = [argv for argv in commands if argv[0].endswith("/ar")]
    assert len(archived) == 1
    # Archive members in the owned archive's order, then the stub; a stray
    # .ll that the owned archive does not contain stays out.
    assert [Path(item).name for item in archived[0][3:]] == [
        "py_obj.o", "py_gen.o", "capture_gc_registers.o"]
    # Not the owned archive's name: nothing presents it as owned output.
    assert archive.name == "libpy_runtime_reference.a"
    assert receipt["label"].startswith("LLVM reference experiment")
    assert receipt["kind"] == kind and receipt["flags"] == flags
    assert receipt["members"] == 2 and receipt["compiler"] == "clang version 20.1.8"
    assert receipt["archive_sha256"] == PERF.hashlib.sha256(archive.read_bytes()).hexdigest()


def test_reference_runtime_fails_explicitly_at_every_gap(tmp_path, monkeypatch):
    arm = tmp_path / "arm"
    arm.mkdir()
    commands, run = _recording_tools(monkeypatch)
    runtime = _owned_runtime(tmp_path, without_ir=("py_gen.o",))
    with pytest.raises(SystemExit, match="py_gen"):
        PERF.build_reference_runtime(arm, runtime, "clang-O2", "arm64-apple-darwin", run=run)
    complete = _owned_runtime(tmp_path / "complete")
    with pytest.raises(SystemExit, match="x86_64-unknown-linux-gnu"):
        PERF.build_reference_runtime(arm, complete, "clang-O2", "x86_64-unknown-linux-gnu", run=run)
    with pytest.raises(SystemExit, match="clang-O3"):
        PERF.build_reference_runtime(arm, complete, "clang-O3", "arm64-apple-darwin", run=run)
    monkeypatch.setattr(PERF.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="clang"):
        PERF.build_reference_runtime(arm, complete, "clang-O2", "arm64-apple-darwin", run=run)


def test_an_arm_seals_its_reference_runtime_kind(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    (repository / "pcc").mkdir(parents=True)
    (repository / "pcc/x.py").write_text("committed\n")
    for args in (["init", "-q"], ["config", "user.email", "perf@example.invalid"],
                 ["config", "user.name", "perf"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True)
    monkeypatch.setattr(PERF, "HOME", tmp_path / "home")
    arm = PERF.seal("lab", (), root=repository, reference_runtime="clang-O2")
    assert json.loads((arm / "source.json").read_text())["reference_runtime"] == "clang-O2"
    plain = PERF.seal("plain", (), root=repository)
    assert json.loads((plain / "source.json").read_text())["reference_runtime"] is None
    with pytest.raises(SystemExit, match="clang-O3"):
        PERF.seal("bad", (), root=repository, reference_runtime="clang-O3")


def test_comparison_labels_rows_that_measure_a_reference_runtime(tmp_path, monkeypatch):
    for name, kind in (("base", None), ("lab", "clang-O2"), ("other", None)):
        arm = tmp_path / name
        arm.mkdir()
        (arm / "source.json").write_text(json.dumps({"reference_runtime": kind}))
    monkeypatch.setattr(PERF, "binary_of", lambda name: tmp_path / name / "native_stats")
    monkeypatch.setattr(PERF, "run_once", lambda *args: _run(100))
    rows = PERF.compare("base", "lab", 1, (0,), (1,), 10)
    assert rows[0]["reference_runtime_by_arm"] == {"base": None, "lab": "clang-O2"}
    rows = PERF.compare("base", "other", 1, (0,), (1,), 10)
    assert "reference_runtime_by_arm" not in rows[0]


def test_reference_runtime_runs_one_llvm_ir_pipeline_before_codegen(tmp_path, monkeypatch):
    runtime = _owned_runtime(tmp_path)
    arm = tmp_path / "arm"
    arm.mkdir()
    commands, run = _recording_tools(monkeypatch)
    _archive, receipt = PERF.build_reference_runtime(
        arm, runtime, "opt:cgscc(inline)", "arm64-apple-darwin", run=run)
    optimized = [argv for argv in commands if argv[0].endswith("/opt")]
    assert sorted(Path(argv[-1]).name for argv in optimized) == ["py_gen.opt.ll", "py_obj.opt.ll"]
    for argv in optimized:
        assert argv[1:3] == ["-S", "-passes=cgscc(inline)"]
    compiles = [argv for argv in commands if "-x" in argv]
    for argv in compiles:
        # Code generation alone, from opt's output.
        assert argv[1:5] == ["-O2", "-Xclang", "-disable-llvm-optzns", "-c"]
        assert argv[-3].endswith(".opt.ll")
    assert receipt["opt_pipeline"] == "cgscc(inline)"


def test_reference_runtime_adds_codegen_options(tmp_path, monkeypatch):
    runtime = _owned_runtime(tmp_path)
    arm = tmp_path / "arm"
    arm.mkdir()
    commands, run = _recording_tools(monkeypatch)
    _archive, receipt = PERF.build_reference_runtime(
        arm, runtime, "llc:regalloc=fast,disable-block-placement", "arm64-apple-darwin", run=run)
    assert not [argv for argv in commands if argv[0].endswith("/opt")]
    assert receipt["flags"] == ["-O2", "-Xclang", "-disable-llvm-optzns",
                                "-mllvm", "-regalloc=fast",
                                "-mllvm", "-disable-block-placement"]
    assert receipt["opt_pipeline"] is None


@pytest.mark.parametrize("kind", ["opt:", "opt:-O2", "llc:", "llc:-regalloc=fast", "llc:a,,b", "gcc"])
def test_reference_runtime_rejects_malformed_kinds(kind):
    with pytest.raises(SystemExit, match="opt:PIPELINE"):
        PERF._reference_plan(kind)


# ---- steps: executed instructions per function, by single-stepping ----------

NM_TEXT = """\
0000000100000000 T __mh_execute_header
0000000100001000 T _alpha
0000000100001040 t _beta
0000000100001080 T _gamma
0000000100002000 S _data_symbol
"""


def test_step_symbols_keep_text_functions_in_address_order():
    symbols = PERF.parse_step_symbols(NM_TEXT)
    assert symbols == [(0x100001000, "alpha"), (0x100001040, "beta"), (0x100001080, "gamma")]


OTOOL_TEXT = """\
Section
  sectname __text
   segname __TEXT
      addr 0x0000000100001000
      size 0x0000000000000100
Section
  sectname __stubs
   segname __TEXT
      addr 0x0000000100001100
      size 0x0000000000000018
Section
  sectname __data
   segname __DATA
      addr 0x0000000100002000
      size 0x0000000000000010
"""


def test_stepped_pcs_count_under_the_enclosing_function_after_the_slide():
    symbols = PERF.parse_step_symbols(NM_TEXT)
    sections = PERF.parse_step_sections(OTOOL_TEXT)
    assert sections == [(0x100001000, 0x100001100, "__text"), (0x100001100, 0x100001118, "__stubs")]
    slide = 0x4000
    pcs = [0x100005000, 0x10000503C, 0x100005040, 0x100005090, 0x100005090, 0x100004FFF,
           0x100005104, 0x100005110, 0x180001000]
    counts = PERF.step_function_counts(symbols, pcs, slide, sections)
    # Linker stubs after the last function are not that function's, and a
    # PC outside the image (a system library) is no function of it.
    assert counts == {"alpha": 2, "beta": 1, "gamma": 2, "<outside>": 2, "<__stubs>": 2}


def test_steps_normalize_the_second_arm_by_shared_program_code():
    first = {"program_a": 600, "program_b": 400, "runtime_x": 900, "<__stubs>": 30}
    second = {"program_a": 300, "program_b": 200, "runtime_x": 300, "runtime_y": 50}
    runtime = {"runtime_x", "runtime_y"}
    scale = PERF.step_program_scale(first, second, runtime)
    # The second window ran half the program work: count it twice.
    assert scale == 2.0
    assert PERF.step_program_scale(first, {"runtime_x": 5}, runtime) is None


def test_step_output_reports_markers_slide_and_pcs():
    text = "markers 3 steps 4 slide 4000 seconds 1.5\n100005000\n100005004\n100005008\n10000500c\n"
    assert PERF.parse_step_output(text) == (3, 0x4000, [0x100005000, 0x100005004, 0x100005008, 0x10000500C])
    with pytest.raises(SystemExit, match="marker"):
        PERF.parse_step_output("markers 0 steps 2 slide 0 seconds 0.1\n1\n2\n")


def test_step_rows_are_per_request_and_ordered_by_largest_change():
    first = {"alpha": 300, "beta": 60, "gamma": 30}
    second = {"alpha": 100, "beta": 80, "delta": 40}
    # The second arm's counts arrive already scaled to the first's work.
    rows = PERF.step_rows(first, 3, second, limit=10)
    assert rows[0] == ("alpha", 100.0, 100 / 3, 100 / 3 - 100.0)
    assert [row[0] for row in rows] == ["alpha", "delta", "gamma", "beta"]
    assert rows[2] == ("gamma", 10.0, 0.0, -10.0)
    assert rows[1] == ("delta", 0.0, 40 / 3, 40 / 3)
    alone = PERF.step_rows(first, 3, None, limit=2)
    assert alone == [("alpha", 100.0, None, None), ("beta", 20.0, None, None)]


def test_step_request_lengths_are_exact_instructions_between_marker_hits():
    # Marker gamma at file 0x100001080; slide 0x4000. Each length is one
    # whole request on the stepped thread, independent of machine load.
    slide = 0x4000
    marker = 0x100005080
    pcs = [0x100005000, marker, 1, 2, 3, marker, 4, 5, 6, marker, 7, 8, 9, marker, 10]
    assert PERF.step_request_lengths(pcs, slide, 0x100001080) == [4, 4, 4]
    assert PERF.step_request_lengths(pcs[:4], slide, 0x100001080) == []
    assert PERF.step_marker_address(PERF.parse_step_symbols(NM_TEXT), "gamma") == 0x100001080
    with pytest.raises(SystemExit, match="marker"):
        PERF.step_marker_address(PERF.parse_step_symbols(NM_TEXT), "missing")


def test_step_callers_count_entries_by_call_site_with_inclusive_lengths():
    # alpha calls gamma twice (one call runs 3 steps, one 5), beta calls it
    # once and the window ends inside it, and a step inside gamma that is
    # not its first instruction is no entry. Slide 0x4000.
    symbols = PERF.parse_step_symbols(NM_TEXT)
    slide = 0x4000
    gamma = 0x100005080
    pcs = [0x100005000, 0x100005004, gamma, gamma + 4, gamma + 8, 0x100005008,
           0x10000500C, gamma, gamma + 4, gamma + 8, gamma + 12, gamma + 16, 0x100005010,
           0x100005044, gamma + 4, 0x100005048, gamma]
    called = PERF.step_callers(symbols, pcs, slide, "gamma")
    assert called == {"alpha": [2, 8, 2], "beta": [1, 0, 0]}
    assert PERF.step_callees(symbols, pcs, slide, "alpha") == {"gamma": [2, 8, 2]}
    assert PERF.step_callees(symbols, pcs, slide, "beta") == {"gamma": [1, 0, 0]}


def test_step_calls_close_tail_calls_and_abandoned_calls_at_an_outer_return():
    # alpha calls beta (returns to alpha+8); beta tail-branches to gamma,
    # which returns straight to alpha+8: both close there. Then alpha calls
    # gamma, which calls beta, and beta returns to alpha+0x14 directly (a
    # switched stack): the abandoned inner call closes with the outer one.
    symbols = PERF.parse_step_symbols(NM_TEXT)
    slide = 0
    alpha, beta, gamma = 0x100001000, 0x100001040, 0x100001080
    pcs = [alpha, alpha + 4, beta, beta + 4, gamma, gamma + 4, alpha + 8,
           alpha + 0x10, gamma, gamma + 4, beta, beta + 4, alpha + 0x14]
    calls = PERF.step_calls(symbols, pcs, slide)
    assert calls == {
        ("alpha", "beta"): [1, 4, 1],
        ("beta", "gamma"): [1, 2, 1],
        ("alpha", "gamma"): [1, 4, 1],
        ("gamma", "beta"): [1, 2, 1],
    }


def test_steps_reuse_a_kept_record_and_print_callers(monkeypatch, tmp_path, capsys):
    record = "markers 2 steps 6 slide 4000 seconds 1.0\n" + "".join(
        f"{pc:x}\n" for pc in (0x100005000, 0x100005080, 0x100005084, 0x100005004,
                               0x100005080, 0x100005088))
    arm = tmp_path / "arm"
    arm.mkdir()
    (arm / "steps-6-gamma.txt").write_text(record)
    monkeypatch.setattr(PERF.sys, "platform", "darwin")
    monkeypatch.setattr(PERF, "arm_dir", lambda name: arm)
    monkeypatch.setattr(PERF, "binary_of", lambda name: arm / "native_stats")

    def fake_run(command, **kwargs):
        text = NM_TEXT if command[1] == "nm" else OTOOL_TEXT
        return subprocess.CompletedProcess(command, 0, stdout=text, stderr="")
    monkeypatch.setattr(PERF.subprocess, "run", fake_run)
    PERF.print_steps(["one"], 6, 0, "gamma", 1, 5, reuse=True, callers="gamma",
                     callees="alpha")
    out = capsys.readouterr().out
    assert "calls alpha makes per request by callee" in out
    assert "     1.0      2.0        2.0  gamma" in out
    # Two entries from alpha: the first returned after 2 steps, the second
    # did not return inside the window.
    assert "one: exact instructions per request 3" in out
    assert "calls of gamma per request by caller" in out
    assert "     1.0      2.0        2.0  alpha" in out


@pytest.mark.parametrize("text,kind", (
    ("stp\tx29, x30, [sp, #-0x10]!", "save/restore"),
    ("ldp\tx19, x20, [sp], #0x20", "save/restore"),
    ("mov\tx29, sp", "frame"),
    ("sub\tsp, sp, #0x20", "frame"),
    ("mov\tx19, x0", "copy"),
    ("mov\tw9, w1", "copy"),
    ("mov\tx10, #0xffff", "immediate"),
    ("movk\tx10, #0xff, lsl #16", "immediate"),
    ("stur\txzr, [x29, #-0x10]", "stack slot"),
    ("ldp\tx1, x2, [x19, #0x10]", "load"),
    ("str\tx1, [x0]", "store"),
    ("casa\tw3, w11, [x2]", "atomic"),
    ("ldar\tx1, [x1]", "atomic"),
    ("adrp\tx9, 2382 ; 0x101a8a000", "address"),
    ("cmp\tx0, #0x0", "compare"),
    ("csel\tx0, x10, x0, eq", "conditional select"),
    ("cbz\tw1, 0x10113c298", "branch"),
    ("b.ne\t0x10113c1e4", "branch"),
    ("eor\tx2, x2, x22, asr #13", "alu"),
))
def test_instructions_are_classified(text, kind):
    assert PERF.instruction_class(text) == kind


def test_step_classes_count_executed_instructions(monkeypatch, tmp_path):
    listing = ("bin:\n(__TEXT,__text) section\n_alpha:\n"
               "0000000100001000\tstp\tx29, x30, [sp, #-0x10]!\n"
               "0000000100001004\tmov\tx19, x0\n"
               "0000000100001008\tret\n")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=listing, stderr="")
    monkeypatch.setattr(PERF.subprocess, "run", fake_run)
    slide = 0x4000
    pcs = [0x100005000, 0x100005004, 0x100005004, 0x100005008, 0x180000000]
    kinds = PERF.step_instruction_classes(tmp_path / "bin", pcs, slide)
    assert kinds == {"save/restore": 1, "copy": 2, "branch": 1, "<outside>": 1}


def test_steps_refuse_hosts_without_lldb(monkeypatch):
    monkeypatch.setattr(PERF.sys, "platform", "linux")
    with pytest.raises(SystemExit, match="Darwin"):
        PERF.main(["steps", "base"])

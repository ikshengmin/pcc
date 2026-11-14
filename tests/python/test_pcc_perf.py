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
    assert PERF.allocations_per_operation(timed, single, 1000) == pytest.approx(3.0)
    assert PERF.allocations_per_operation(single, single, 1000) == 0.0


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
    arm = PERF.seal("probe", ("pcc/runtime",), root=repository)
    source = arm / "source"
    assert (source / "pcc/runtime/mine.py").read_text() == "my edit\n"
    assert (source / "pcc/runtime/new.py").read_text() == "my new file\n"
    assert not (source / "pcc/runtime/gone.py").exists()
    assert (source / "pcc/other.py").read_text() == "committed\n"
    assert (source / "scripts/tool.py").read_text() == "committed\n"
    assert not (source / "scripts/unfinished.py").exists()
    assert not (source / "docs").exists()
    identity = json.loads((arm / "source.json").read_text())
    assert identity["overlaid"] == [
        "pcc/runtime/gone.py", "pcc/runtime/mine.py", "pcc/runtime/new.py"]
    assert sorted(identity["left_out"]) == ["pcc/other.py", "scripts/unfinished.py"]
    assert set(identity["files"]) == {
        "pcc/runtime/mine.py", "pcc/runtime/new.py", "pcc/other.py", "scripts/tool.py"}
    assert b"my edit" in (arm / "worktree.diff").read_bytes()
    assert b"another session" not in (arm / "worktree.diff").read_bytes()
    with pytest.raises(SystemExit):
        PERF.seal("probe", (), root=repository)


def test_arm_passes_its_preflight_reserve_and_reports_a_refused_start(tmp_path, monkeypatch, capsys):
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
    assert PERF.main(["arm", "probe", "--max-tree-rss", "4294967296",
                      "--preflight-reserve", "536870912"]) == 1
    argv = seen[0]
    assert argv[argv.index("--max-tree-rss-bytes") + 1] == "4294967296"
    assert argv[argv.index("--darwin-preflight-reserve-bytes") + 1] == "536870912"
    assert "build did not start (guard exit 2)" in capsys.readouterr().out
    seen.clear()
    PERF.main(["arm", "probe"])
    assert seen[0][seen[0].index("--darwin-preflight-reserve-bytes") + 1] == "8589934592"

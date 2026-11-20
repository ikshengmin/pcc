"""Public max-producer lifetimes at the first explicit collection.

The existing hostile-iterator regression collects inside its producers and
twice at each disposal checkpoint. Here the program makes exactly two explicit
calls overall, and does not assert when or in what order finalizers run between them.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


_PROGRAM = '''"""max producer: three independently visible lifetimes at each collection boundary."""
import gc
import sys
import weakref

a_refs = []
b_refs = []
holder = []
control = []
entered = []
drops = []


class Item:
    def __init__(self, name, rank):
        self.name = name
        self.rank = rank
        self.self_cycle = self

    def __lt__(self, other):
        return self.rank < other.rank

    def __del__(self):
        drops.append(self.name)


class Source:
    def __init__(self, name, low, high):
        self.name = name
        self.values = [low, high]
        self.index = 0
        self.self_cycle = self

    def __iter__(self):
        return self

    def __next__(self):
        if self.index == len(self.values):
            raise StopIteration
        item = self.values[self.index]
        self.index += 1
        return item

    def __del__(self):
        drops.append(self.name)


def make_group(label, witnesses, retain_low):
    low = Item(label + "-low", 1)
    high = Item(label + "-high", 9)
    source = Source(label + "-source", low, high)
    witnesses.append(weakref.ref(source))
    witnesses.append(weakref.ref(low))
    witnesses.append(weakref.ref(high))
    if retain_low:
        control.append(low)
    return source


def maximum(values):
    return max(values)


def stage_held():
    holder.append(maximum(make_group("A", a_refs, False)))


def raise_marker(marker):
    raise marker


def take(*, value, later):
    entered.append("entered")


def stage_error(retain_low):
    marker = RuntimeError("late keyword")
    try:
        take(value=maximum(make_group("B", b_refs, retain_low)), later=raise_marker(marker))
    except RuntimeError as caught:
        assert caught is marker, "later keyword error replaced"
    else:
        raise AssertionError("later keyword did not raise")
    marker = None


def check_first():
    assert len(entered) == 0, "callee entered after failing keyword"
    assert a_refs[0]() is None and a_refs[1]() is None, "A source or loser still live"
    assert a_refs[2]() is holder[0] and holder[0].name == "A-high", "A winner identity lost"
    assert drops.count("A-source") == 1 and drops.count("A-low") == 1, "A disposal count"
    assert drops.count("A-high") == 0, "A held winner finalized"
    assert b_refs[0]() is None and b_refs[2]() is None, "B source or winner still live"
    assert drops.count("B-source") == 1 and drops.count("B-high") == 1, "B source/winner count"
    assert b_refs[1]() is None, "B loser still live at first collection"
    assert drops.count("B-low") == 1 and len(drops) == 5, "B loser or total count"


def check_second():
    for witness in a_refs:
        assert witness() is None, "A witness still live"
    for witness in b_refs:
        assert witness() is None, "B witness still live"
    for name in ("A-source", "A-low", "A-high", "B-source", "B-low", "B-high"):
        assert drops.count(name) == 1, "final disposal count"
    assert len(drops) == 6, "unexpected disposal"


def main():
    gc.disable()
    arm = sys.argv[1]
    assert arm == "normal" or arm == "retain-loser"
    stage_held()
    stage_error(arm == "retain-loser")
    # Both constructing helpers and the exception handler have returned.
    gc.collect()
    check_first()
    # The identity check's temporary reference has also left scope.
    holder.clear()
    gc.collect()
    check_second()
    print("MAX_FIRST_COLLECT_OK")


main()
'''


_SUCCESS = "MAX_FIRST_COLLECT_OK\n"


def _write_program(directory: Path) -> Path:
    source = directory / "max_first_collect.py"
    source.write_bytes(_PROGRAM.encode("utf-8"))
    return source


@pytest.mark.parametrize("arm", ("normal", "retain-loser"))
def test_max_first_collection_reference(tmp_path: Path, arm: str) -> None:
    ran = run_process_group_timeout(
        [sys.executable, "-I", "-B", str(_write_program(tmp_path)), arm],
        env=dict(os.environ, NO_COLOR="1"), timeout=10,
    )
    if arm == "normal":
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, _SUCCESS, "")
    else:
        # This arm changes the input graph, not the assertion: B's source and
        # high item must already have expired before the retained loser fails.
        assert ran.returncode == 1 and not ran.stdout, ran
        assert ran.stderr.splitlines()[-1] == "AssertionError: B loser still live at first collection"


@pytest.mark.integration
def test_max_first_collection_native_all_gc(tmp_path: Path, explicit_owned_runtime: Path) -> None:
    from pcc.diagnostics.gc_log import parse_log_lines
    from pcc.frontends.python.pipeline import compile_python
    from scripts.owned_image_linkage import inspect_owned_image

    app = tmp_path / "standalone"
    app.mkdir()
    source = _write_program(app)
    executable = tmp_path / ("max_first_collect.exe" if os.name == "nt" else "max_first_collect")
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime))
    assert inspect_owned_image(executable.read_bytes())["libpython"] == "off"
    for backend in range(5):
        log = tmp_path / f"gc{backend}.jsonl"
        ran = run_process_group_timeout(
            [str(executable), "normal"],
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="gc",
                     PCC_LOG_FORMAT="json", PCC_LOG_FILE=str(log)), timeout=10,
        )
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, _SUCCESS, ""), (backend, ran)
        # Prove the collector that actually ran, without making log event
        # count, finalizer order or the integer returned by collect() an oracle.
        observed = {event.fields["value1"] for event in parse_log_lines(log.read_text().splitlines())
                    if event.fields.get("category") == "gc" and event.event == "collect_stop"}
        assert observed == {backend}, (backend, observed)

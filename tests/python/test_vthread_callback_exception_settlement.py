"""Test eventual callback operand settlement separately from PCC's immediate-disposal contract."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pytest

from tests.python.process_timeout import run_process_group_timeout


# Host and native share every semantic check. Only PCC runs real scheduling;
# CPython's reference calls handler directly through the repository's vt.call.
_BEFORE_YIELD = '''"""One fresh process per arm: scheduler_case.py callback|argument."""
import gc
import sys
import weakref
import pcc.virtual_thread as vt

selected = []
references = []
events = []


class Token:
    def __del__(self):
        events.append("token-drop")
        raise RuntimeError("unraisable token disposal")


class Callback:
    def __call__(self, token, value):
        events.append("callback-enter")
        gc.collect()
        assert value == 2
        assert references[0]() is token
        raise selected[0]

    def __del__(self):
        events.append("callback-drop")
        raise RuntimeError("unraisable callback disposal")


def on_weakref(reference):
    events.append("weakref-drop")
    raise RuntimeError("unraisable weakref disposal")


def make_token():
    token = Token()
    references.append(weakref.ref(token, on_weakref))
    return token


def leaf(failing_argument):
    gc.collect()
    # The pending call still owns its operands during argument evaluation.
    assert references[0]() is not None
    if failing_argument:
        raise selected[0]
    return 2


def during_unwind(phase, info):
    if phase == "start":
        events.append("gc-hook")
        raise RuntimeError("unraisable collection callback")


def handler(failing_argument):
'''

_UNWIND = '''    try:
        try:
            vt.call(Callback(), make_token(), leaf(failing_argument))
        finally:
            # A known user callback raises under the *propagating* exception,
            # independent of when either operand becomes unreachable.
            gc.callbacks.append(during_unwind)
            try:
                gc.collect()
            finally:
                gc.callbacks.remove(during_unwind)
    except ValueError as caught:
        assert caught is selected[0], "selecting exception replaced"
        events.append("caught-selected")
        return 17
    raise AssertionError("selecting exception disappeared")


'''

_NATIVE_DRIVE = '''def drive(failing_argument):
    task = vt.spawn(handler, failing_argument)
    vt.run(1, 128)
    assert vt.outcome(task) == vt.OUTCOME_RETURNED
    assert vt.result(task) == 17
    task = None


'''

_CHECK_SETTLEMENT = '''def settled(failing_argument):
    assert events.count("callback-enter") == (0 if failing_argument else 1), "wrong call boundary"
    assert events.count("gc-hook") == 1, "collection callback missed or duplicated"
    assert events.count("caught-selected") == 1, "selecting exception not caught"
    assert events.count("token-drop") == 1, "token disposal count"
    assert events.count("callback-drop") == 1, "callable disposal count"
    assert events.count("weakref-drop") == 1, "weak callback count"
    assert references[0]() is None, "token still owned"


def main():
    gc.disable()
    arm = sys.argv[1]
    assert arm == "callback" or arm == "argument"
    failing_argument = arm == "argument"
    selected.append(ValueError("selected callback failure"))
    drive(failing_argument)
    # Leave the exception handler and the scheduler's result/handle scope
    # before releasing the external exception owner. No earlier disposal
    # counts, and no required order among the object's three notifications.
    selected.clear()
    gc.collect()
    settled(failing_argument)
    # A later collection cannot repair an earlier missed return boundary.
    gc.collect()
    settled(failing_argument)
    print("settled", arm)


'''

_PROGRAM = _BEFORE_YIELD + "    vt.yield_now()\n" + _UNWIND + _NATIVE_DRIVE + _CHECK_SETTLEMENT + "main()\n"
_HOST_DRIVE = """def drive(failing_argument):
    assert handler(failing_argument) == 17


"""
_HOST_MAIN = """seen = set()
def note_unraisable(report):
    seen.add(str(report.exc_value))
sys.unraisablehook = note_unraisable
main()
assert {"unraisable token disposal", "unraisable callback disposal",
        "unraisable weakref disposal", "unraisable collection callback"} <= seen
"""
_HOST = _BEFORE_YIELD + _UNWIND + _HOST_DRIVE + _CHECK_SETTLEMENT + _HOST_MAIN
_ARMS = ("callback", "argument")


def _run_host(tmp_path: Path, program: str, arm: str):
    # -I ignores ambient imports. Add only this checkout for the real vt.call.
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "host_reference.py"
    source.write_text(f"import sys\nsys.path.insert(0, {str(root)!r})\n" + program, encoding="utf-8")
    return run_process_group_timeout(
        [sys.executable, "-I", "-B", str(source), arm],
        env=dict(os.environ, NO_COLOR="1"), timeout=10,
    )


@pytest.mark.parametrize("arm", _ARMS)
def test_callback_settlement_host_adapter(tmp_path: Path, arm: str) -> None:
    result = _run_host(tmp_path, _HOST, arm)
    assert (result.returncode, result.stdout, result.stderr) == (0, f"settled {arm}\n", "")


@pytest.mark.parametrize(("arm", "failure"), (
    ("callback", "selecting exception replaced"),
    ("argument", "token disposal count"),
))
def test_callback_settlement_host_discriminators(tmp_path: Path, arm: str, failure: str) -> None:
    source = _HOST
    if arm == "callback":
        before = "        raise selected[0]\n\n    def __del__"
        after = '        raise ValueError("replacement")\n\n    def __del__'
    else:
        # Retain the actual token explicitly, not the selecting exception:
        # that control should not depend on CPython's internal ownership.
        before = "    references.append(weakref.ref(token, on_weakref))"
        after = before + "\n    retained.append(token)"
        source = "retained = []\n" + source
    assert source.count(before) == 1
    result = _run_host(tmp_path, source.replace(before, after), arm)
    assert result.returncode == 1 and not result.stdout, result
    assert result.stderr.splitlines()[-1] == "AssertionError: " + failure, result.stderr


@pytest.mark.integration
def test_callback_settlement_native_all_gc(tmp_path: Path, pcc_runtime_archive: Path) -> None:
    from pcc.diagnostics.gc_log import parse_log_lines
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_packages import resolve_module_src_for_import

    executable = tmp_path / ("settlement.exe" if os.name == "nt" else "settlement")
    # Package lookup walks parent directories. Place the app outside the
    # checkout, not beside pcc/virtual_thread.py (which is the host adapter).
    with TemporaryDirectory(prefix="pcc-vt-settlement-") as app_dir:
        assert resolve_module_src_for_import(app_dir, "pcc.virtual_thread") is None, (
            "standalone fixture needs a temp root without a sibling pcc/virtual_thread.py"
        )
        source = Path(app_dir) / "scheduler_case.py"
        source.write_bytes(_PROGRAM.encode("utf-8"))
        compile_python(str(source), str(executable), backend="self",
                       ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        for arm in _ARMS:
            log = tmp_path / f"gc{backend}-{arm}.jsonl"
            ran = run_process_group_timeout(
                [str(executable), arm], timeout=10,
                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                         PCC_LOG="gc", PCC_LOG_FORMAT="json", PCC_LOG_FILE=str(log)),
            )
            assert (ran.returncode, ran.stdout) == (0, f"settled {arm}\n"), (backend, arm, ran)
            # Require proof of the actual collector, not an exact number or order
            # of collections. Unraisable diagnostics on stderr are permitted.
            modes = {event.fields["value1"] for event in parse_log_lines(log.read_text().splitlines())
                     if event.fields.get("category") == "gc" and event.event == "collect_stop"}
            assert modes == {backend}, (backend, arm, modes)

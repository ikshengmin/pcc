"""Derived-class uniqueness needs only the first two occurrences of each base.

These tests preserve mapping order, duplicate occurrences and input evaluation
(Python Language Reference 6.2.7, 6.16 and 8.3). Retained candidate counts are
an internal shape assertion, not a measurement of time or process memory.
"""

from copy import deepcopy
from inspect import getsource
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline_context import (
    _closed_world_derived_class_map,
    build_closed_world_context,
)


CASES = [
    ({}, []),
    ({"empty": {}}, []),
    ({"m": {"value": None, "function": {"kind": "function", "base_names": None},
            "missing": {"base_names": None}, "empty": {"kind": "class"}}}, []),
    ({"m": {"C": {"kind": "class", "base_names": ("Base",)}}},
     [("Base", ("m", "C"))]),
    ({"m": {"C": {"kind": "class", "base_names": ("Base", "Base")}}}, []),
    ({"m": {"C": {"kind": "class", "base_names": ("Base",) * 128}}}, []),
    ({"m": {"C": {"kind": "class", "base_names": ["Z", "A", "Z", "B", "Z"]}}},
     [("A", ("m", "C")), ("B", ("m", "C"))]),
    ({"one": {"C": {"kind": "class", "base_names": ("Shared", "First")}},
      "two": {"C": {"kind": "class", "base_names": ("Shared", "Second")}},
      "three": {"C": {"kind": "class", "base_names": ("Shared", "Third")}}},
     [("First", ("one", "C")), ("Second", ("two", "C")), ("Third", ("three", "C"))]),
    ({"m": {"C": {"kind": "class", "base_names": (1, True, 1, 2)}}},
     [(2, ("m", "C"))]),
    ({"m": {"C": {"kind": "class", "base_names": "abaca"}}},
     [("b", ("m", "C")), ("c", ("m", "C"))]),
    ({"m": {"A": {"kind": "class", "base_names": ("B",)},
            "B": {"kind": "class", "base_names": ("A",)},
            "Self": {"kind": "class", "base_names": ("Self",)}}},
     [("B", ("m", "A")), ("A", ("m", "B")), ("Self", ("m", "Self"))]),
]


@pytest.mark.parametrize("exports, expected", CASES)
def test_unique_candidates_preserve_results_order_and_inputs(exports, expected):
    before = deepcopy(exports)
    actual = _closed_world_derived_class_map(exports)
    assert list(actual.items()) == expected
    assert list(actual.items()) == list(_legacy_derived_class_map(exports).items())
    assert exports == before
    assert actual is not exports


@pytest.mark.parametrize("count", [32, 256])
def test_long_chains_keep_every_unique_edge_in_order(count):
    exports = {"m": {}}
    expected = []
    for index in range(count):
        base, derived = "C" + str(index), "C" + str(index + 1)
        exports["m"][derived] = {"kind": "class", "base_names": (base,)}
        expected.append((base, ("m", derived)))
    assert list(_closed_world_derived_class_map(exports).items()) == expected
    assert list(_legacy_derived_class_map(exports).items()) == expected


@pytest.mark.parametrize("alias_count", [1, 2, 3, 64])
def test_reexport_aliases_are_occurrences_not_unique_objects(alias_count):
    shared = {"kind": "class", "base_names": ("Base",)}
    exports = {"provider": {"Original": shared}}
    for index in range(alias_count - 1):
        exports["alias" + str(index)] = {"Imported": shared}
    actual = _closed_world_derived_class_map(exports)
    assert actual == ({"Base": ("provider", "Original")} if alias_count == 1 else {})
    assert actual == _legacy_derived_class_map(exports)
    assert all(info is shared for module in exports.values() for info in module.values())


def _capture_candidates(function, exports):
    captured = []
    previous = sys.gettrace()

    def trace(frame, event, value):
        if frame.f_code is function.__code__:
            if event == "return":
                captured.append(frame.f_locals["base_to_derived"])
            return trace
        return None

    try:
        sys.settrace(trace)
        result = function(exports)
    finally:
        sys.settrace(previous)
    assert len(captured) == 1
    return result, captured[0]


@pytest.mark.parametrize("count", [1, 2, 3, 128])
def test_ambiguous_bases_retain_only_the_first_two_candidates(count):
    exports = {"m": {}}
    for index in range(count):
        exports["m"]["C" + str(index)] = {"kind": "class", "base_names": ("Shared",)}
    exports["m"]["Only"] = {"kind": "class", "base_names": ("Unique",)}
    before = deepcopy(exports)
    expected, legacy = _capture_candidates(_legacy_derived_class_map, exports)
    actual, retained = _capture_candidates(_closed_world_derived_class_map, exports)
    assert list(actual.items()) == list(expected.items())
    assert list(retained) == list(legacy)
    for base in legacy:
        assert retained[base] == legacy[base][:2]
        assert len(retained[base]) == min(len(legacy[base]), 2)
    assert len(legacy["Shared"]) == count
    assert exports == before


@pytest.mark.parametrize("bad", [None, 42, [], {"kind": "class", "base_names": None},
                                {"kind": "class", "base_names": 42},
                                {"kind": "class", "base_names": ("Shared", [])}])
def test_malformed_late_input_preserves_exception_type_and_message(bad):
    # A malformed module value is not skipped just because Shared is ambiguous.
    exports = {"first": {"C": {"kind": "class", "base_names": ("Shared",) * 3}}}
    if isinstance(bad, dict):
        exports["last"] = {"Bad": bad}
    else:
        exports["last"] = bad
    before = deepcopy(exports)
    with pytest.raises(Exception) as reference:
        _legacy_derived_class_map(exports)
    with pytest.raises(type(reference.value)) as result:
        _closed_world_derived_class_map(exports)
    assert str(result.value) == str(reference.value)
    assert exports == before


def _observable_exports(events, fail_at):
    class Key:
        def __init__(self, label, group):
            self.label = label
            self.group = group

        def __hash__(self):
            record(("hash", self.label))
            return 7

        def __eq__(self, other):
            record(("eq", self.label, other.label))
            return self.group == other.group

    def record(event):
        events.append(event)
        if len(events) == fail_at:
            raise ValueError("event " + str(fail_at))

    class Bases:
        def __iter__(self):
            for label, group in (("a", 0), ("b", 0), ("c", 0), ("d", 0), ("e", 1)):
                record(("yield", label))
                yield Key(label, group)
            record(("exhausted",))

    class Info(dict):
        def get(self, name, default=None):
            record(("get", name))
            return super().get(name, default)

    class Module(dict):
        def items(self):
            record(("module-items",))
            return super().items()

    class Exports(dict):
        def items(self):
            record(("exports-items",))
            return super().items()

    return Exports(m=Module(C=Info(kind="class", base_names=Bases())))


def _observed(function, fail_at):
    events = []
    try:
        result = function(_observable_exports(events, fail_at))
    except ValueError as error:
        return events, ("error", str(error))
    return events, [(key.label, value) for key, value in result.items()]


def test_input_callbacks_and_every_injected_failure_keep_the_same_order():
    events, expected = _observed(_legacy_derived_class_map, 0)
    assert expected == [("e", ("m", "C"))]
    assert ("exhausted",) in events
    assert _observed(_closed_world_derived_class_map, 0) == (events, expected)
    for fail_at in range(1, len(events) + 1):
        assert _observed(_closed_world_derived_class_map, fail_at) == _observed(
            _legacy_derived_class_map, fail_at
        )


PROVIDER = """class Shared:
    pass
class Unique:
    pass
class Keep:
    pass
class One(Unique):
    pass
class First(Shared):
    pass
class Second(Shared):
    pass
class Third(Shared):
    pass
class Last(Keep):
    pass
"""
CONSUMER = """import provider as p
from provider import One as Imported
class Fourth(p.Shared):
    pass
class Tail(p.Last):
    pass
"""
EXPECTED_CONTEXT = [
    ("Keep", ("provider", "Last")),
    ("provider.Shared", ("consumer", "Fourth")),
    ("provider.Last", ("consumer", "Tail")),
]


def _write_context_sources(tmp_path):
    provider, consumer = tmp_path / "provider.py", tmp_path / "consumer.py"
    provider.write_text(PROVIDER, encoding="utf-8")
    consumer.write_text(CONSUMER, encoding="utf-8")
    return [str(provider), str(consumer)]


def test_real_context_preserves_reexport_alias_ambiguity(tmp_path):
    _modules, exports, actual = build_closed_world_context(
        _write_context_sources(tmp_path), ["provider", "consumer"]
    )
    assert exports["provider"]["One"] is exports["consumer"]["Imported"]
    assert list(actual.items()) == EXPECTED_CONTEXT
    assert list(actual.items()) == list(_legacy_derived_class_map(exports).items())


NATIVE_BODY = """
def check(exports):
    actual = _closed_world_derived_class_map(exports)
    expected = _legacy_derived_class_map(exports)
    assert list(actual.items()) == list(expected.items())
    return actual

def main():
    assert check({}) == {}
    shared = {'kind': 'class', 'base_names': ('Shared',)}
    exports = {'m': {}}
    for index in range(128):
        exports['m']['C' + str(index)] = shared
    exports['m']['Duplicate'] = {'kind': 'class', 'base_names': ('Repeat', 'Repeat', 'Repeat')}
    exports['m']['Only'] = {'kind': 'class', 'base_names': ('Z', 'A')}
    assert list(check(exports).items()) == [('Z', ('m', 'Only')), ('A', ('m', 'Only'))]
    assert exports['m']['C0'] is shared
    assert exports['m']['C127'] is shared
    for index in range(3):
        bad = {'m': {'C': {'kind': 'class', 'base_names': ('Shared', 'Shared', 'Shared')}}}
        if index == 0:
            bad['m']['Bad'] = {'kind': 'class', 'base_names': None}
        elif index == 1:
            bad['m']['Bad'] = {'kind': 'class', 'base_names': 42}
        else:
            bad['m']['Bad'] = {'kind': 'class', 'base_names': ([],)}
        caught = 0
        try:
            _legacy_derived_class_map(bad)
        except TypeError:
            caught += 1
        try:
            _closed_world_derived_class_map(bad)
        except TypeError:
            caught += 1
        assert caught == 2
    modules, exports, actual = build_closed_world_context(
        [sys.argv[1], sys.argv[2]], ['provider', 'consumer']
    )
    assert len(modules) == 2
    assert exports['provider']['One'] is exports['consumer']['Imported']
    assert list(actual.items()) == EXPECTED_CONTEXT
    assert list(actual.items()) == list(check(exports).items())
    _write_native_exports_wire(sys.argv[3], exports, actual)
    print('DERIVED_CANDIDATES_OK')

if __name__ == '__main__':
    main()
"""


@pytest.mark.integration
def test_native_candidates_and_real_context_match_same_source_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    program = (
        "import sys\n"
        "from pcc.frontends.python.pipeline_context import (\n"
        "    _closed_world_derived_class_map, build_closed_world_context,\n"
        ")\n"
        "from pcc.frontends.python.pipeline_exports import _write_native_exports_wire\n"
        + getsource(_legacy_derived_class_map)
        + f"\nEXPECTED_CONTEXT = {EXPECTED_CONTEXT!r}\n"
        + NATIVE_BODY
    )
    paths = _write_context_sources(tmp_path)
    source, executable = tmp_path / "derived_program.py", tmp_path / "derived_program"
    source.write_text(program, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    wire = tmp_path / "reference.json"
    reference = subprocess.run([sys.executable, str(source), *paths, str(wire)],
                               cwd=tmp_path, env=env, capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stderr == ""
    assert reference.stdout == "DERIVED_CANDIDATES_OK\n"
    expected_wire = wire.read_bytes()
    python_program_compiler(str(source), str(executable), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="on")
    for collector in ("0", "1", "2", "3", "4"):
        wire = tmp_path / ("native_gc" + collector + ".json")
        result = subprocess.run([str(executable), *paths, str(wire)], cwd=tmp_path,
                                env={**os.environ, "PCC_GC_BACKEND": collector},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == "", collector
        assert result.stdout == reference.stdout, collector
        assert wire.read_bytes() == expected_wire, collector


# Frozen 65e4b85 helper; only the function name differs.
def _legacy_derived_class_map(native_exports):
    base_to_derived = {}
    for derived_mod, exports in native_exports.items():
        for class_name, info in exports.items():
            if not isinstance(info, dict) or info.get("kind") != "class":
                continue
            for base_name in info.get("base_names", ()):
                base_to_derived.setdefault(base_name, []).append(
                    (derived_mod, class_name)
                )
    derived_class_map = {}
    for base_name, derived_list in base_to_derived.items():
        if len(derived_list) == 1:
            derived_class_map[base_name] = derived_list[0]
    return derived_class_map

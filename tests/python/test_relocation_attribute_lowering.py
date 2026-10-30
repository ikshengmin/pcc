"""Host-only negative result; no native program or collector is executed."""

import importlib.util
import os
from pathlib import Path

import pytest

from pcc.backend import macho_obj, macho_spec


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "relocation_attribute_lowering", ROOT / "scripts/research/relocation_attribute_lowering.py",
)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def proposal():
    namespace = dict(vars(macho_obj))
    exec(experiment.PROPOSAL, namespace)
    return namespace[experiment.PROPOSAL_NAME]


def reference(rows):
    count = len(rows)
    for relocation in rows:
        if relocation.addend:
            count += 1
        if relocation.type == macho_spec.ARM64_RELOC_SUBTRACTOR:
            count += 1
    return count


@pytest.mark.parametrize("kind,addend", [(0, 0), (0, 4), (1, 0)])
def test_proposal_keeps_count_for_real_relocation_records(kind, addend):
    rows = [macho_obj.Relocation(0, "_symbol", kind, False, 3, addend)]
    assert proposal()(rows) == reference(rows)


def test_subclass_and_duck_keep_dynamic_field_access_order():
    events = []

    class Derived(macho_obj.Relocation):
        def __getattribute__(self, name):
            if name in ("addend", "type"):
                events.append(name)
            return super().__getattribute__(name)

    class Duck:
        @property
        def addend(self):
            events.append("addend")
            return 9

        @property
        def type(self):
            events.append("type")
            return macho_spec.ARM64_RELOC_SUBTRACTOR

    rows = [Derived(0, "_symbol", 0, False, 3, 7), Duck()]
    expected = reference(rows)
    expected_events = events[:]
    events.clear()
    assert proposal()(rows) == expected
    assert events == expected_events == ["addend", "type", "addend", "type"]


def test_dynamic_error_is_not_reordered():
    events = []

    class Raising:
        @property
        def addend(self):
            events.append("first")
            raise ValueError("original addend failure")

        @property
        def type(self):
            raise AssertionError("type read after failed addend")

    for implementation in (reference, proposal()):
        with pytest.raises(ValueError, match="^original addend failure$"):
            implementation([Raising()])
    assert events == ["first", "first"]


@pytest.mark.parametrize("line", ["strict.nolib.stub:", "  call ptr @py_cpy_getattr(ptr null)"])
def test_text_admission_rejects_unavailable_body(line):
    # Parser-utility negatives only, not evidence about the real compiler.
    text = "define ptr @probe() {\n" + line + "\n  ret ptr null\n}"
    with pytest.raises(ValueError, match="unavailable without libpython"):
        experiment.function_text(text, "probe")


def test_actual_current_lowering_does_not_elide_the_condition_reads():
    repo = Path(os.environ.get("PCC_RESEARCH_REPO", ROOT))
    report = experiment.run(repo)
    baseline, proposed, existing = (report["cases"][name] for name in (
        "baseline", "proposed", "existing_append",
    ))
    for row in (baseline, proposed, existing):
        assert row["owned_ir_verified"]
        assert row["flags"]["_strict_no_libpython"]
        assert not row["flags"]["_runtime_port_module"]
        assert not row["flags"]["_freestanding_module"]
    assert baseline["attribute_sites"]["addend"] == baseline["attribute_sites"]["type"] == 1
    assert proposed["exact_arm_attribute_sites"]["addend"] == 1
    assert proposed["exact_arm_attribute_sites"]["type"] == 1
    assert proposed["attribute_sites"]["addend"] == proposed["attribute_sites"]["type"] == 2
    assert proposed["py_type_builtin_sites"] == 1
    assert existing["attribute_sites"]["addend"] == existing["attribute_sites"]["type"] == 1
    # The old helper is not wholly ineffective: its other field projections
    # still use indexed access. Do not generalize this conditional-read result.
    assert existing["py_instance_get_field_sites"] > 0

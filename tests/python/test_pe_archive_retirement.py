"""Host ownership controls; these do not execute native GC or measure its cost."""

import weakref

import pytest

from pcc.backend import coff_x86_64 as coff
from pcc.backend import pe_host_budget as host
from pcc.backend import pe_x86_64 as pe
from pcc.backend.ar_writer import write_archive
from tests.python.test_pe_stackmap_merge import _object, _pe_budget


def _fixture(unselected):
    entry = _object("pcc_windows_start")
    entry = coff.CoffObject(
        entry.sections,
        entry.symbols + (coff.CoffSymbol("needed_worker", 0),),
    )
    first = _object("needed_worker")
    first = coff.CoffObject(
        first.sections,
        first.symbols + (coff.CoffSymbol("dependency", 0),),
    )
    dependency = _object("dependency")
    # Place the dependency before its requester: selection must rescan in
    # original archive order rather than merely keep a sequential suffix.
    members = [dependency, first]
    for index in unselected:
        members.insert(index, _object("unused_" + str(index), ordinary=b"x" * 4096))
    archive = write_archive([
        (str(index) + ".o", coff.emit_object(member))
        for index, member in enumerate(members)
    ])
    return entry, first, dependency, archive


def _observe_parsed(monkeypatch):
    original = pe.parse_object
    parsed = []

    def parse(payload):
        obj = original(payload)
        parsed.append((
            obj.symbols[0].name,
            weakref.ref(obj),
            tuple(weakref.ref(section) for section in obj.sections),
            tuple(weakref.ref(symbol) for symbol in obj.symbols),
        ))
        return obj

    monkeypatch.setattr(pe, "parse_object", parse)
    return parsed


def _assert_all_live(parsed):
    assert parsed
    for _name, owner, sections, symbols in parsed:
        assert owner() is not None
        assert all(ref() is not None for ref in sections + symbols)


def _assert_retired(parsed, selected):
    assert parsed
    for name, owner, sections, symbols in parsed:
        if name.startswith("unused_"):
            assert owner() is None
            assert all(ref() is None for ref in sections + symbols)
        else:
            assert any(owner() is obj for obj in selected)
            assert all(ref() is not None for ref in sections + symbols)


@pytest.mark.parametrize("unselected", [(), (0,), (1,), (2,), (0, 2, 4)])
@pytest.mark.parametrize("admission", ["none", "explicit", "automatic"])
def test_archive_census_retires_after_admission_before_link(
    monkeypatch, unselected, admission,
):
    entry, first, dependency, archive = _fixture(unselected)
    budget = None if admission == "none" else _pe_budget(work=1000)
    if budget is not None:
        budget.retained_bytes = 19
    # Byte oracle: the same selected members, in actual rescan order, with
    # no unused archive graph. This executes the ordinary PE/link consumer.
    monkeypatch.setattr(host, "host_pe_budget", lambda: None)
    expected = pe.link_executable([entry, first, dependency], compact_budget=budget)
    if budget is not None:
        expected_work = budget.record_visits
        budget.record_visits = 0
    parsed = _observe_parsed(monkeypatch)
    events = []

    def automatic_budget():
        _assert_all_live(parsed)
        events.append("baseline")
        return budget

    monkeypatch.setattr(host, "host_pe_budget", automatic_budget)
    if budget is not None:
        prepare = budget.prepare

        def prepare_while_live(objects, imports):
            _assert_all_live(parsed)
            events.append("prepare")
            return prepare(objects, imports)

        monkeypatch.setattr(budget, "prepare", prepare_while_live)
    link = pe._link_selected_objects

    def link_after_retirement(objects, *args):
        _assert_retired(parsed, objects)
        events.append("link")
        return link(objects, *args)

    monkeypatch.setattr(pe, "_link_selected_objects", link_after_retirement)
    inputs = [entry]
    archives = [archive]
    actual = pe.link_executable(
        inputs, archives=archives,
        compact_budget=budget if admission == "explicit" else None,
    )
    assert actual == expected
    assert inputs == [entry] and inputs[0] is entry
    assert archives == [archive] and archives[0] is archive
    assert events == {
        "none": ["baseline", "link"],
        "explicit": ["prepare", "link"],
        "automatic": ["baseline", "prepare", "link"],
    }[admission]
    if budget is not None:
        assert budget.retained_bytes == 19
        assert budget.record_visits == expected_work


def test_post_selection_failure_does_not_retain_unused_members(monkeypatch):
    entry, _first, _dependency, archive = _fixture((0, 2, 4))
    parsed = _observe_parsed(monkeypatch)
    budget = _pe_budget()
    budget.retained_bytes = 19
    error = coff.CoffError("selected link failure")

    def fail_after_retirement(objects, *args):
        _assert_retired(parsed, objects)
        raise error

    monkeypatch.setattr(pe, "_link_selected_objects", fail_after_retirement)
    with pytest.raises(coff.CoffError) as caught:
        pe.link_executable([entry], archives=[archive], compact_budget=budget)
    assert caught.value is error
    assert budget.retained_bytes == 19
    # Even a retained traceback cannot revive the retired selection graph.
    for name, owner, sections, symbols in parsed:
        if name.startswith("unused_"):
            assert owner() is None
            assert all(ref() is None for ref in sections + symbols)


def test_preparation_failure_preserves_admission_and_cleanup(monkeypatch):
    entry, _first, _dependency, archive = _fixture((2,))
    parsed = _observe_parsed(monkeypatch)
    budget = _pe_budget(limit=100)
    budget.retained_bytes = 19
    prepare = budget.prepare
    entered = []

    def reject_before_retirement(objects, imports):
        _assert_all_live(parsed)
        entered.append("prepare")
        return prepare(objects, imports)

    def unexpected_link(*args):
        pytest.fail("a rejected preparation must not enter selected linking")

    monkeypatch.setattr(budget, "prepare", reject_before_retirement)
    monkeypatch.setattr(pe, "_link_selected_objects", unexpected_link)
    with pytest.raises(coff.CoffError, match="budget rejected"):
        pe.link_executable([entry], archives=[archive], compact_budget=budget)
    assert entered == ["prepare"]
    assert budget.retained_bytes == 19 and budget.record_visits == 0

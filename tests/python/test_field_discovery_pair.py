"""Finite same-source discovery reuse; no native program execution."""
import pytest

from pcc.frontends.python import pipeline_dependency_closure as closure
from pcc.frontends.python import pipeline_import_policy as policy
from pcc.frontends.python import py_lift


SOURCES = (
    "value = 1\n",
    "from dataclasses import dataclass, field\n@dataclass\nclass Box:\n    rows: list = field(default_factory=list)\n",
    "from dataclasses import field\ndef build():\n    return field(default_factory=list)\n",
    "from dataclasses import dataclass, field as make_field\n@dataclass\nclass Box:\n    rows: list = make_field(default_factory=list)\n",
    "from dataclasses import field as make_field\nvalue = make_field\n",
    "from dataclasses import dataclass, field as a\nfrom dataclasses import field as b\n@dataclass\nclass Box:\n    rows: list = a(default_factory=list)\nvalue = b\n",
    "def build():\n    from dataclasses import field\n    return field(default_factory=list)\n",
    "from dataclasses import dataclass\nfrom os import path\n",
)
SOURCE_IDS = ("none", "consumed", "runtime", "alias_consumed", "alias_runtime", "mixed_aliases", "lazy_binding", "no_field")


def _uncached_pair(source):
    return (
        closure._source_absolute_imports_for_discovery(source, include_function_bodies=False),
        closure._source_absolute_imports_for_discovery(source, include_function_bodies=True),
    )


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_actual_complete_source_matches_uncached_modes(source):
    expected = _uncached_pair(source)
    actual = closure._source_absolute_import_discovery_pair(source)
    assert actual == expected
    assert actual[0] is not actual[1]


@pytest.mark.parametrize("index", [1, 2, 3, 4], ids=["consumed", "runtime", "alias_consumed", "alias_runtime"])
def test_private_state_retains_only_source_and_success_boolean(monkeypatch, index):
    source = SOURCES[index]
    state = []
    expected = bool(policy.dataclasses_field_runtime_names(py_lift.parse_and_lift(source, "<field-import-scan>", "<scan>")))
    expected_pair = _uncached_pair(source)
    parse_calls = []
    real_parse = py_lift.pp.parse

    def counted_parse(*args, **kwargs):
        # Observation only: leave both guarded reader identities unchanged.
        parse_calls.append(args[0])
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(py_lift.pp, "parse", counted_parse)
    first = closure._source_absolute_imports_for_discovery_with_field_state(
        source, include_function_bodies=False, field_state=state,
    )
    assert first == expected_pair[0] and state == [source, expected]
    assert type(state[0]) is str and type(state[1]) is bool
    second = closure._source_absolute_imports_for_discovery_with_field_state(
        source, include_function_bodies=True, field_state=state,
    )
    assert second == expected_pair[1]
    assert parse_calls == [source]
    assert len(state) == 2
    parse_calls.clear()
    assert closure._source_absolute_import_discovery_pair(source) == expected_pair
    assert parse_calls == [source]
    parse_calls.clear()
    assert _uncached_pair(source) == expected_pair
    assert parse_calls == [source, source]


@pytest.mark.parametrize("second_raises", [False, True], ids=["returns", "raises"])
def test_replaced_public_hook_is_reloaded_between_modes(monkeypatch, second_raises):
    events = []

    def second(source, *, include_function_bodies=True):
        events.append(("second", source, include_function_bodies))
        if second_raises:
            raise ValueError("second discovery failure")
        return ["lazy"]

    def first(source, *, include_function_bodies=True):
        events.append(("first", source, include_function_bodies))
        monkeypatch.setattr(closure, "_source_absolute_imports_for_discovery", second)
        return ["eager"]

    monkeypatch.setattr(closure, "_source_absolute_imports_for_discovery", first)
    if second_raises:
        with pytest.raises(ValueError, match="^second discovery failure$"):
            closure._source_absolute_import_discovery_pair("source")
    else:
        assert closure._source_absolute_import_discovery_pair("source") == (["eager"], ["lazy"])
    assert events == [("first", "source", False), ("second", "source", True)]


@pytest.mark.parametrize("reader", ["parser", "classifier"])
@pytest.mark.parametrize("failure", ["none", "first", "both"])
def test_replaced_readers_keep_calls_and_conservative_failures(monkeypatch, reader, failure):
    module, name = (py_lift, "parse_and_lift") if reader == "parser" else (policy, "dataclasses_field_runtime_names")
    real = getattr(module, name)
    events = []

    def changed(*args):
        events.append(reader)
        if failure == "both" or (failure == "first" and len(events) == 1):
            raise ValueError("reader failure")
        return real(*args)

    monkeypatch.setattr(module, name, changed)
    expected = _uncached_pair(SOURCES[1])
    expected_events = list(events)
    events.clear()
    assert closure._source_absolute_import_discovery_pair(SOURCES[1]) == expected
    assert events == expected_events == [reader, reader]


@pytest.mark.parametrize("reader", ["parser", "classifier"])
def test_missing_canonical_identity_falls_back_without_changing_result(monkeypatch, reader):
    module, name = (py_lift, "_FIELD_DISCOVERY_PARSE_AND_LIFT") if reader == "parser" else (policy, "_FIELD_DISCOVERY_RUNTIME_NAMES")
    monkeypatch.delattr(module, name)
    source = SOURCES[1]
    expected = _uncached_pair(source)
    assert closure._source_absolute_import_discovery_pair(source) == expected
    state = []
    assert closure._source_absolute_imports_for_discovery_with_field_state(
        source, include_function_bodies=True, field_state=state,
    ) == expected[1]
    assert state == []


def test_filtered_complete_source_not_mode_or_alias_is_the_cache_key(monkeypatch):
    remaining = [SOURCES[1], SOURCES[2]]

    def filtering(source):
        return remaining.pop(0)

    monkeypatch.setattr(closure, "_without_inactive_runtime_imports", filtering)
    expected = _uncached_pair("same original source")
    remaining[:] = [SOURCES[1], SOURCES[2]]
    assert closure._source_absolute_import_discovery_pair("same original source") == expected
    assert expected == ([], ["dataclasses", "dataclasses.field"])
    assert remaining == []

    # Same string, but a public reader changes between modes: the previous
    # successful result must not suppress the replacement classifier.
    original_classifier = policy.dataclasses_field_runtime_names
    events = []

    def replacement_classifier(module):
        events.append("replacement classifier")
        return ("field",)

    for call_pair in (_uncached_pair, closure._source_absolute_import_discovery_pair):
        monkeypatch.setattr(policy, "dataclasses_field_runtime_names", original_classifier)
        filtering_calls = []

        def same_source_with_reader_swap(source):
            filtering_calls.append(source)
            if len(filtering_calls) == 2:
                monkeypatch.setattr(policy, "dataclasses_field_runtime_names", replacement_classifier)
            return source

        monkeypatch.setattr(closure, "_without_inactive_runtime_imports", same_source_with_reader_swap)
        events.clear()
        assert call_pair(SOURCES[1]) == ([], ["dataclasses", "dataclasses.dataclass", "dataclasses.field"])
        assert events == ["replacement classifier"]
        assert filtering_calls == [SOURCES[1], SOURCES[1]]


def test_real_parse_failure_does_not_populate_state():
    source = "from dataclasses import field\ndef unfinished(\n"
    state = []
    assert closure._source_absolute_imports_for_discovery_with_field_state(
        source, field_state=state,
    ) == closure._source_absolute_imports_for_discovery(source)
    assert state == []
    assert closure._source_absolute_import_discovery_pair(source) == _uncached_pair(source)


def test_first_mode_reader_callback_replaces_second_public_hook(monkeypatch):
    real = py_lift.parse_and_lift
    canonical = closure._SOURCE_ABSOLUTE_IMPORTS_FOR_DISCOVERY
    events = []

    def second(source, *, include_function_bodies=True):
        events.append(("replacement", include_function_bodies))
        return ["changed"]

    def parser(*args):
        events.append(("parser",))
        monkeypatch.setattr(closure, "_source_absolute_imports_for_discovery", second)
        return real(*args)

    monkeypatch.setattr(py_lift, "parse_and_lift", parser)
    expected = _uncached_pair(SOURCES[1])
    expected_events = list(events)
    events.clear()
    monkeypatch.setattr(closure, "_source_absolute_imports_for_discovery", canonical)
    assert closure._source_absolute_import_discovery_pair(SOURCES[1]) == expected
    assert events == expected_events == [("parser",), ("replacement", True)]


def test_custom_source_preserves_first_error_before_second_mode(monkeypatch):
    events = []

    class Source(str):
        def splitlines(self, *args, **kwargs):
            events.append("splitlines")
            raise ValueError("original splitlines error")

    monkeypatch.setattr(closure, "_without_inactive_runtime_imports", lambda source: source)
    monkeypatch.setattr(closure, "_source_import_discovery_text", lambda source: source)
    with pytest.raises(ValueError, match="^original splitlines error$"):
        closure._source_absolute_import_discovery_pair(Source("value = 1\n"))
    assert events == ["splitlines"]

    class State(list):
        def __len__(self):
            raise AssertionError("custom state must not be read")

        def __getitem__(self, key):
            raise AssertionError("custom state must not be read")

        def clear(self):
            raise AssertionError("custom state must not be written")

        def append(self, value):
            raise AssertionError("custom state must not be written")

    source = SOURCES[2]
    expected = _uncached_pair(source)
    state = State()
    actual = tuple(
        closure._source_absolute_imports_for_discovery_with_field_state(
            source, include_function_bodies=mode, field_state=state,
        )
        for mode in (False, True)
    )
    assert actual == expected

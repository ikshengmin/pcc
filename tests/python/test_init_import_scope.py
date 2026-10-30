"""Adjacent init-order scope reuse; finite host source readers, no native run."""
from __future__ import annotations

import builtins

import pytest

from pcc.frontends.python import pipeline_dependency_closure as closure
from pcc.frontends.python import pipeline_import_scan as scan


SOURCES = (
    "",
    "import alpha, beta as chosen\nfrom pkg import first, second as renamed\n",
    "if enabled:\n    import eager\nelse:\n    from pkg import item\ndef lazy():\n    import deferred\n",
    "class Box:\n    import class_only\n    from pkg import deferred\nimport final\n",
    "def lazy(\n    value,\n):\n    import deferred\nfrom pkg import eager\n",
    "from pkg import (\n    first, # comment\n    second as renamed,\n)\nimport final\n",
    "from pkg import first, \\\n    second\nfrom unfinished import (\n    tail\n",
    "# import ignored\ntext = 'from fictional import name'\ntry:\n    import eager\nexcept Exception:\n    from pkg import fallback\n",
)


def _uncached(source):
    return (
        scan._iter_source_import_specs(source, top_level_only=True),
        scan._iter_source_import_from_specs(source, top_level_only=True),
    )


@pytest.mark.parametrize("source", SOURCES, ids=["empty", "aliases", "control_flow", "class_body", "multiline_def", "multiline_from", "continuation_and_incomplete", "comments_and_handler"])
def test_owned_scope_reuse_matches_uncached_readers(source):
    expected = _uncached(source)
    state = []
    imports = scan._iter_source_import_specs_with_scope_state(
        source, top_level_only=True, scope_state=state,
    )
    assert len(state) == 2 and state[0] == source
    rows = state[1]
    assert type(rows) is list
    assert all(type(row) is tuple and type(row[0]) is str and type(row[1]) is bool for row in rows)
    froms = scan._iter_source_import_from_specs_with_scope_state(
        source, top_level_only=True, scope_state=state,
    )
    assert (imports, froms) == expected
    assert state[1] is rows
    # Returned statement lists are separate from the shared row list.
    imports.append("not a source row")
    froms.clear()
    assert state[1] == scan._source_module_scope_lines(source)


def test_direct_scanners_keep_fresh_rows(monkeypatch):
    seen = []
    original = scan._source_import_lines_with_scope_state

    def observe(source, top_level_only, state):
        rows = original(source, top_level_only, state)
        seen.append((state, rows))
        return rows

    monkeypatch.setattr(scan, "_source_import_lines_with_scope_state", observe)
    _uncached(SOURCES[1])
    assert len(seen) == 2 and all(state is None for state, _ in seen)
    assert seen[0][1] == seen[1][1] and seen[0][1] is not seen[1][1]


@pytest.mark.parametrize("custom", ["source", "state"])
def test_custom_source_and_state_do_not_reuse(custom):
    class Source(str):
        def splitlines(self, *args, **kwargs):
            calls.append("splitlines")
            return super().splitlines(*args, **kwargs)

    class State(list):
        def __len__(self):
            raise AssertionError("custom state was inspected")
        def clear(self):
            raise AssertionError("custom state was changed")

    calls = []
    source = Source(SOURCES[1]) if custom == "source" else SOURCES[1]
    state = [] if custom == "source" else State()
    expected = _uncached(source)
    calls.clear()
    actual = (
        scan._iter_source_import_specs_with_scope_state(source, top_level_only=True, scope_state=state),
        scan._iter_source_import_from_specs_with_scope_state(source, top_level_only=True, scope_state=state),
    )
    assert actual == expected
    if custom == "source":
        assert calls == ["splitlines", "splitlines"] and state == []


@pytest.mark.parametrize("replacement", ["scope", "missing_identity"])
def test_replaced_scope_reader_and_missing_identity_fall_back(monkeypatch, replacement):
    calls = []
    original = scan._source_module_scope_lines
    if replacement == "scope":
        def changed(source, **kwargs):
            calls.append(source)
            return original(source, **kwargs)
        monkeypatch.setattr(scan, "_source_module_scope_lines", changed)
    else:
        monkeypatch.delattr(scan, "_INIT_IMPORT_SCOPE_LINES")
    state = []
    assert scan._iter_source_import_specs_with_scope_state(SOURCES[1], top_level_only=True, scope_state=state) == ["alpha", "beta"]
    assert scan._iter_source_import_from_specs_with_scope_state(SOURCES[1], top_level_only=True, scope_state=state) == [("pkg", ["first", "second"])]
    assert state == []
    if replacement == "scope":
        assert calls == [SOURCES[1], SOURCES[1]]


def test_changed_source_or_mode_does_not_reuse_rows():
    state = []
    first = "import eager\ndef f():\n    import lazy\n"
    scan._iter_source_import_specs_with_scope_state(first, top_level_only=True, scope_state=state)
    rows = state[1]
    assert scan._iter_source_import_specs_with_scope_state(first, top_level_only=False, scope_state=state) == ["eager", "lazy"]
    assert state[1] is rows
    second = "from pkg import changed\n"
    assert scan._iter_source_import_from_specs_with_scope_state(second, top_level_only=True, scope_state=state) == [("pkg", ["changed"])]
    assert state[0] == second and state[1] is not rows


@pytest.mark.parametrize("raises", [False, True], ids=["returns", "raises"])
def test_first_hook_replaces_second_hook(monkeypatch, tmp_path, raises):
    source = tmp_path / "entry.py"
    source.write_text(SOURCES[1], encoding="utf-8")
    events = []

    def second(text, *, top_level_only):
        events.append(("second", text, top_level_only))
        if raises:
            raise ValueError("second hook")
        return [("pkg", ["leaf"])]

    def first(text, *, top_level_only):
        events.append(("first", text, top_level_only))
        monkeypatch.setattr(closure, "_iter_source_import_from_specs", second)
        return ["pkg"]

    monkeypatch.setattr(closure, "_iter_source_import_specs", first)
    monkeypatch.setattr(closure, "_relative_import_targets", lambda *a, **k: [])
    args = ("entry", {"entry": str(source)}, {"entry", "pkg", "pkg.leaf"}, {})
    if raises:
        with pytest.raises(ValueError, match="^second hook$"):
            closure._order_module_init_deps_for(*args)
    else:
        assert closure._order_module_init_deps_for(*args) == ["pkg", "pkg.leaf"]
    assert events == [("first", SOURCES[1], True), ("second", SOURCES[1], True)]


def test_reader_swap_during_first_dependency_processing(monkeypatch, tmp_path):
    source = tmp_path / "entry.py"
    source.write_text("import pkg\nfrom pkg import old\n", encoding="utf-8")
    events = []
    admitted = {"entry", "pkg", "pkg.changed"}

    def changed_scope(text, **kwargs):
        events.append("changed scope")
        return [("from pkg import changed", True)]

    class Members:
        def __contains__(self, key):
            monkeypatch.setattr(scan, "_source_module_scope_lines", changed_scope)
            return key in admitted

    monkeypatch.setattr(closure, "_relative_import_targets", lambda *a, **k: [])
    assert closure._order_module_init_deps_for("entry", {"entry": str(source)}, Members(), {}) == ["pkg", "pkg.changed"]
    assert events == ["changed scope"]


@pytest.mark.parametrize("failure", ["none", "import", "dependency", "from", "relative"])
def test_owner_releases_rows_before_relative_and_on_error(monkeypatch, tmp_path, failure):
    source = tmp_path / "entry.py"
    source.write_text("import pkg\nfrom pkg import leaf\n", encoding="utf-8")
    states = []
    events = []
    original = closure._init_import_specs

    def observe_first(text, state):
        states.append(state)
        result = original(text, state)
        assert len(state) == 2
        events.append("import")
        if failure == "import":
            raise ValueError("import")
        return result

    def relative(*args, **kwargs):
        events.append("relative")
        assert states and states[0] == []
        if failure == "relative":
            raise ValueError("relative")
        return []

    def from_hook(text, *, top_level_only):
        events.append("from")
        raise ValueError("from")

    class Members:
        def __contains__(self, key):
            if failure == "dependency":
                events.append("dependency")
                raise ValueError("dependency")
            return key in {"entry", "pkg", "pkg.leaf"}

    monkeypatch.setattr(closure, "_init_import_specs", observe_first)
    monkeypatch.setattr(closure, "_relative_import_targets", relative)
    if failure == "from":
        monkeypatch.setattr(closure, "_iter_source_import_from_specs", from_hook)
    cache = {}
    if failure == "none":
        assert closure._order_module_init_deps_for("entry", {"entry": str(source)}, Members(), cache) == ["pkg", "pkg.leaf"]
        assert cache == {"entry": ["pkg", "pkg.leaf"]}
        assert events == ["import", "relative"]
    else:
        with pytest.raises(ValueError, match="^" + failure + "$"):
            closure._order_module_init_deps_for("entry", {"entry": str(source)}, Members(), cache)
        assert cache == {}
        assert events[-1] == failure
    assert states == [[]]


GRAPHS = (
    {"pkg.entry": "import pkg.a\n", "pkg.a": "from pkg.b import value\n", "pkg.b": "value = 1\n"},
    {"pkg.entry": "from pkg.a import value\n", "pkg.a": "import pkg.b\n", "pkg.b": "import pkg.a\n", "pkg.other": "value = 1\n"},
    {"pkg.entry": "import pkg.a, pkg.a\nfrom pkg.b import value\n", "pkg.a": "from .leaf import value\n", "pkg.b": "from .leaf import value\n", "pkg.leaf": "value = 1\n"},
    {"pcc.client": "from pcc.ir.compat import ir_c as ir\n", "pcc.ir.ir": "value = 1\n"},
)


@pytest.mark.parametrize("graph", GRAPHS, ids=["chain", "cycle_and_unreachable", "duplicate_diamond", "compat_provider"])
def test_actual_init_order_matches_uncached_graph(monkeypatch, tmp_path, graph):
    paths = []
    for module, text in graph.items():
        path = tmp_path.joinpath(*module.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    if "pcc.client" in graph:
        (tmp_path / "pcc" / "ir" / "compat.py").write_text("ir_c = None\n", encoding="utf-8")
    modules = list(graph)
    actual = closure._order_module_inits(paths, modules, modules[0])
    with monkeypatch.context() as fallback:
        fallback.setattr(closure, "_INIT_IMPORT_SPECS", None)
        fallback.setattr(closure, "_INIT_IMPORT_FROM_SPECS", None)
        expected = closure._order_module_inits(paths, modules, modules[0])
    assert actual == expected
    assert modules[0] not in actual and len(actual) == len(set(actual)) == len(modules) - 1
    if "pcc.client" in graph:
        assert actual == ["pcc.ir.ir"]
    elif "pkg.leaf" in graph:
        assert actual.index("pkg.leaf") < actual.index("pkg.a") < actual.index("pkg.b")
    elif "pkg.other" not in graph:
        assert actual == ["pkg.b", "pkg.a"]


def test_actual_reread_cache_and_read_failure_order(monkeypatch, tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    path = package / "entry.py"
    path.write_text("import pkg.first\n", encoding="utf-8")
    (package / "changed.py").write_text("value = 1\n", encoding="utf-8")
    reads = []
    real_open = builtins.open

    def observed_open(name, *args, **kwargs):
        reads.append(str(name))
        if len(reads) == 2 and path.exists():
            path.write_text("from .changed import value\n", encoding="utf-8")
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr(closure, "open", observed_open, raising=False)
    cache = {}
    members = {"pkg.entry", "pkg.first", "pkg.changed"}
    deps = closure._order_module_init_deps_for("pkg.entry", {"pkg.entry": str(path)}, members, cache)
    assert deps == ["pkg.first", "pkg.changed"] and reads == [str(path), str(path)]
    assert closure._order_module_init_deps_for("pkg.entry", {}, members, cache) is deps
    assert len(reads) == 2
    reads.clear()
    path.unlink()
    with pytest.raises(FileNotFoundError):
        closure._order_module_init_deps_for("pkg.entry", {"pkg.entry": str(path)}, members, {})
    assert reads == [str(path), str(path)]

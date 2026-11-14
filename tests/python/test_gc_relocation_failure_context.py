"""Host contracts for failure-only GC4 probe diagnostics.

Owned C lowering checks the complete probe, but does not link or run a runtime.
Synthetic failing runner results test receipt plumbing, not relocation success.
The admitted-runtime integration probe remains the positive native oracle.
"""

import ast
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.python import test_gc_relocation_sweep_verdict as probe


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"


def _without_comments(source):
    # Keep literal text and source offsets: a comment must not supply a fake
    # definition or executable check to this source contract.
    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*'
    return re.sub(
        pattern,
        lambda match: re.sub(r"[^\n]", " ", match[0])
        if match[0].startswith(("/*", "//")) else match[0],
        source, flags=re.S,
    )


def _c_tokens(source):
    return re.findall(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|[0-9]+|[^\s]',
        _without_comments(source),
    )


def _probe_body(name, source=None, *, noinline=False):
    if source is None:
        source = probe.PROBE_SOURCE
    # This fixture's definitions close at column zero. Bound every check to
    # one definition, not the rest of the translation unit or a dead helper.
    pattern = (
        r"^(?P<attribute>__attribute__\(\(noinline\)\) )?"
        r"(?:static )?(?:int|void) " + re.escape(name)
        + r"\([^;]*?\) \{\n(?P<body>.*?)^\}"
    )
    matches = list(re.finditer(pattern, _without_comments(source), re.M | re.S))
    assert len(matches) == 1, name
    match = matches[0]
    if noinline:
        assert match["attribute"] is not None, name
    return source[match.start("body"):match.end("body")]


# Fixed expectations for the real protocol, including complete control flow.
# These are not derived from PROBE_SOURCE at test time. Full token equality
# rejects early returns, conditional bypasses, wrong bindings and reordered
# calls even when all old assertion strings survive in unreachable code.
_EXPECTED_PROTOCOL = {
    'probe_scrub_stack': r"""
    volatile unsigned char words[65536];
    for (int i = 0; i < (int)sizeof(words); i++) words[i] = 0;
""",
    'build_probe': r"""
    struct PyClassObject *value_class =
        py_class_new("SweepVerdictValue", 0, 0, 0, 0);
    if (value_class == 0) return 3;
    pcc_gc_pin((PyObject *)value_class);
    py_class_add_method(
        value_class, "__del__", (PyObject *)(uintptr_t)probe_del
    );
    PyObject *source = py_instance_new(value_class);
    if (source == 0) return 4;
    pcc_gc_store_root(&root_slot, source);
    py_decref(source);
    root_handle = pcc_gc_scheduler_root_register_handle(&root_slot);
    if (root_handle == 0) return 5;
    py_header_flags_or(py_header(source), PY_FLAG_GC_SWEEP_CANDIDATE);
    source_holder = source;
    return 0;
""",
    'relocate_probe': r"""
    pcc_gc_reset_relocation_set();
    if (pcc_gc_select_relocation_set(1) != 1)
        return fail_relocation_state(13, "select", source_holder, 0, -1);
    PyObject *source = source_holder;
    if (pcc_gc_relocation_set_contains(source) != 1)
        return fail_relocation_state(13, "selected-other-object", source, 0, -1);
    if ((flags_of(source) & PY_FLAG_GC_SWEEP_CANDIDATE) == 0)
        return fail_state(29, "source-lost-injected-verdict", source, 0);
    int64_t requested_size = instance_payload_size(source);
    PyObject *copy = pcc_gc_relocate_copy(
        source, requested_size
    );
    if (copy != 0) {
        py_decref(copy);
    }
    if (copy == 0 || copy == source)
        return fail_relocation_state(14, "relocate", source, copy, requested_size);
    watched_copy = copy;
    if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)
        return fail_state(20, "destination-inherits-verdict", source, copy);
    if (
        backend == PCC_GC_KIND_COLORED_RELOCATING
        && (flags_of(source) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0
    ) {
        return fail_state(21, "shell-keeps-verdict", source, copy);
    }
    return 0;
""",
    'verify_live_copy': r"""
    PyObject *source = 0;
    PyObject *copy = root_slot;
    if (copy != watched_copy)
        return fail_state(28, "owner-not-remapped-to-copy", source, copy);
    (void)pcc_gc_collect(0);
    if (del_other != 0)
        return fail_state(27, "finalizer-ran-on-forwarding-shell", 0, 0);
    if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)
        return fail_state(22, "collect-recut-live-copy", source, copy);
    if (del_on_copy != 0)
        return fail_state(23, "finalizer-ran-on-live-copy", source, copy);
    if (pcc_gc_object_is_known_no_lock(copy) != 1)
        return fail_state(24, "live-copy-unknown-after-collect", source, copy);
    return 0;
""",
    'release_probe': r"""
    pcc_gc_scheduler_root_unregister_handle(root_handle);
    pcc_gc_store_root(&root_slot, 0);
    root_slot = 0;
""",
    'main': r"""
    const int32_t backend = @BACKEND@;
    if (pcc_threads_enabled() != @EXPECTED_THREADS@) return 6;
    if (pcc_refcount_strategy() != @EXPECTED_REFCOUNT_STRATEGY@) return 1;
    if (pcc_gc_set_backend(backend) != 0) return 2;
    int result = build_probe();
    if (result != 0) return result;
    probe_scrub_stack();
    if (pcc_stop_the_world() != 0) return 90;
    result = relocate_probe(backend);
    source_holder = 0;
    int resume_result = pcc_resume_world();
    if (result != 0) return result;
    if (resume_result != 0) return 91;
    result = verify_live_copy();
    if (result != 0) return result;
    release_probe();
    for (int i = 0; i < 16 && del_on_copy == 0; i++) {
        probe_scrub_stack();
        (void)pcc_gc_collect(0);
    }
    if (backend == PCC_GC_KIND_COLORED_RELOCATING) {
        if (del_on_copy != 1)
            return fail_state(25, "exactly-once-violated", 0, 0);
    } else if (pcc_gc_object_is_known_no_lock(watched_copy) != 0) {
        return fail_state(26, "dead-copy-still-known", 0, 0);
    }
    if (del_other != 0)
        return fail_state(27, "finalizer-ran-on-forwarding-shell", 0, 0);
    printf(
        "ok backend=%d del_other=%ld\n",
        backend,
        (long)del_other
    );
    return 0;
""",
}


def _assert_lifetime_protocol(source):
    # The witness has only includes, never preprocessor rerouting of a
    # checked helper or call. A same-named dead definition cannot hide that.
    assert re.findall(r"(?m)^\s*#\s*(\w+)", _without_comments(source)) == [
        "include", "include", "include",
    ]
    for name, expected in _EXPECTED_PROTOCOL.items():
        body = _probe_body(name, source, noinline=name != "main")
        assert _c_tokens(body) == _c_tokens(expected), name


# Fixed bindings/control flow: dead, conditional or unlocked reads must not
# satisfy the diagnostic contract merely because the call names are present.
_EXPECTED_STABLE_SNAPSHOT = r"""
    /* Keep the original failure line before any diagnostic graph-lock entry.
     * These are post-failure observations, NOT the first rejected copy phase.
     * Even acquiring the graph lock can service a pending stop. */
    int result = fail_state(code, what, source, copy);
    pcc_py_gc_minor_graph_lock();
    int64_t pending_before = pcc_gc_alloc_stage_pending();
    int64_t selected = pcc_gc_relocation_set_contains(source);
    int64_t selection_size = pcc_gc_relocation_set_size();
    /* Public scalar totals for this selection epoch, not object identities. */
    int64_t candidate_count = pcc_gc_backend4_evacuation_candidate_score();
    int64_t candidate_bytes = pcc_gc_backend4_evacuation_candidate_bytes();
    /* Stable lookup does not drain a staged allocation on an index miss.
     * The enclosing graph lock owns the node lifetime through these reads. */
    void *node = pcc_gc_object_index_find_stable(source);
    int published = node != 0;
    int64_t freeing = node ? pcc_gc_object_node_freeing(node) : -1;
    /* Raw node size, even if freeing; not the object-known-size predicate. */
    int64_t published_size = node ? pcc_gc_object_node_size(node) : 0;
    void *forwarding_record = pcc_gc_forwarding_find(source);
    int64_t is_forwarding_target = pcc_gc_forwarding_target_exists(source);
    int32_t source_flags = flags_of(source);
    int32_t source_tag = py_header(source)->type_tag;
    int64_t pending_after = pcc_gc_alloc_stage_pending();
    pcc_py_gc_minor_graph_unlock();
    fprintf(
        stderr,
        "relocation_context=post-failure-stable-index code=%d "
        "pending_before=%lld selected=%lld selection_size=%lld "
        "candidate_count=%lld candidate_bytes=%lld "
        "published_node=%d node_freeing=%lld published_node_size=%lld "
        "forwarding_record=%p source_is_forwarding_target=%lld "
        "src_flags=0x%x header_pinned=%d src_tag=%d "
        "requested_size=%lld pending_after=%lld\n",
        code, (long long)pending_before, (long long)selected,
        (long long)selection_size, (long long)candidate_count,
        (long long)candidate_bytes, published, (long long)freeing,
        (long long)published_size, forwarding_record,
        (long long)is_forwarding_target, (unsigned)source_flags,
        (source_flags & PY_FLAG_GC_PINNED) != 0, source_tag,
        (long long)requested_size, (long long)pending_after
    );

"""


def _assert_candidate_snapshot(source):
    first = _probe_body("fail_relocation_state", source).split(
        "    /* This secondary predicate", 1
    )[0]
    assert _c_tokens(first) == _c_tokens(_EXPECTED_STABLE_SNAPSHOT)


_CANDIDATE_GETTERS = (
    ("pcc_gc_backend4_evacuation_candidate_score", "pcc_gc_backend4_evacuation_candidates"),
    ("pcc_gc_backend4_evacuation_candidate_bytes", "pcc_gc_backend4_evacuation_candidate_bytes_count"),
)


def _assert_candidate_getters(runtime, public):
    tree = ast.parse(runtime)
    for symbol, counter in _CANDIDATE_GETTERS:
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == symbol]
        expected = ast.parse(
            f'@c_abi_export("{symbol}")\n'
            f'def {symbol}() -> int:\n'
            f'    return load_i32(global_addr("{counter}"), 0)\n'
        ).body[0]
        assert len(functions) == 1
        # Exact exported scalar load: no allocation, traversal, stop service,
        # staging drain, selection retry or nested graph-lock acquisition.
        assert ast.dump(functions[0]) == ast.dump(expected)
        assert f"int64_t {symbol}(void);" in " ".join(public.split())


def _diagnostic():
    return _probe_body("fail_relocation_state")


def test_diagnostics_are_only_on_original_selection_and_copy_failures():
    main = _probe_body("main")
    relocation = _probe_body("relocate_probe", noinline=True)
    # The two selection failures and one copy failure are distinct, live
    # branches in relocate_probe. The complete call graph is checked below.
    assert relocation.count("fail_relocation_state(") == 3
    assert relocation.count("pcc_gc_select_relocation_set(") == 1
    assert relocation.count("instance_payload_size(") == 1
    assert relocation.count("pcc_gc_relocate_copy(") == 1
    assert '''    if (pcc_gc_select_relocation_set(1) != 1)
        /* Size was not requested on this path; -1 means not evaluated. */
        return fail_relocation_state(13, "select", source_holder, 0, -1);
    PyObject *source = source_holder;
    if (pcc_gc_relocation_set_contains(source) != 1)
        return fail_relocation_state(13, "selected-other-object", source, 0, -1);''' in relocation
    assert '''    int64_t requested_size = instance_payload_size(source);
    PyObject *copy = pcc_gc_relocate_copy(
        source, requested_size
    );''' in relocation
    assert '''    if (copy == 0 || copy == source)
        return fail_relocation_state(14, "relocate", source, copy, requested_size);
    watched_copy = copy;''' in relocation
    for name in ("main", "build_probe", "relocate_probe", "verify_live_copy", "release_probe"):
        body = _probe_body(name)
        assert "pcc_gc_alloc_stage_pending" not in body
        assert "pcc_gc_object_index_find" not in body
        assert "pcc_py_gc_minor_graph_lock" not in body
        assert "pcc_gc_object_is_address_pinned" not in body
        if name != "relocate_probe":
            assert "fail_relocation_state(" not in body
            assert "pcc_gc_select_relocation_set(" not in body
            assert "pcc_gc_relocate_copy(" not in body
    assert "PyObject *" not in main
    assert "pcc_gc_relocation_set_add" not in probe.PROBE_SOURCE
    assert "pcc_gc_unpin" not in probe.PROBE_SOURCE


def test_original_movement_verdict_lifetime_and_exactly_once_checks_remain():
    _assert_lifetime_protocol(probe.PROBE_SOURCE)
    # Keep mutations inside this existing node: the host node inventory is
    # unchanged. Each still leaves the original helpers/checks in the source.
    mutations = (
        ("#include <stdio.h>", "#include <stdio.h>\n#define relocate_probe(backend) 0"),
        ("words[i] = 0;", "words[i] = 1;"),
        ("    result = relocate_probe(backend);", ""),
        ("    result = relocate_probe(backend);", "    if (0) result = relocate_probe(backend);"),
        ("    result = relocate_probe(backend);", "    return 0;\n    result = relocate_probe(backend);"),
        ("result = relocate_probe(backend);", "result = relocate_probe(0);"),
        ("result = verify_live_copy();", "result = build_probe();"),
        ("    release_probe();", "    if (0) release_probe();"),
        ("    if (result != 0) return result;", "    if (result != 0) return 0;"),
        ("    if (resume_result != 0) return 91;", "    if (resume_result == 0) return 91;"),
        ("    pcc_gc_reset_relocation_set();", "    return 0;\n    pcc_gc_reset_relocation_set();"),
        ("PyObject *source = source_holder;", "PyObject *source = root_slot;"),
        ("    PyObject *copy = root_slot;", "    PyObject *copy = watched_copy;"),
        ("        py_decref(copy);", ""),
        ('return fail_state(29, "source-lost-injected-verdict", source, 0);', "return 0;"),
        ('return fail_state(20, "destination-inherits-verdict", source, copy);', "return 0;"),
        ('return fail_state(21, "shell-keeps-verdict", source, copy);', "return 0;"),
        ('return fail_state(28, "owner-not-remapped-to-copy", source, copy);', "return 0;"),
        ('return fail_state(22, "collect-recut-live-copy", source, copy);', "return 0;"),
        ('return fail_state(23, "finalizer-ran-on-live-copy", source, copy);', "return 0;"),
        ('return fail_state(24, "live-copy-unknown-after-collect", source, copy);', "return 0;"),
        ('return fail_state(25, "exactly-once-violated", 0, 0);', "return 0;"),
        ('return fail_state(26, "dead-copy-still-known", 0, 0);', "return 0;"),
        ('return fail_state(27, "finalizer-ran-on-forwarding-shell", 0, 0);', "return 0;"),
        ("    pcc_gc_store_root(&root_slot, 0);", "    pcc_gc_store_root(&source_holder, 0);"),
    )
    for original, replacement in mutations:
        assert original in probe.PROBE_SOURCE
        changed = probe.PROBE_SOURCE.replace(original, replacement, 1)
        with pytest.raises(AssertionError):
            _assert_lifetime_protocol(changed)
    for name in ("build_probe", "relocate_probe", "verify_live_copy", "release_probe", "probe_scrub_stack"):
        declaration = re.search(
            r"__attribute__\(\(noinline\)\) static (?:int|void) " + name,
            probe.PROBE_SOURCE,
        )[0]
        changed = probe.PROBE_SOURCE.replace(declaration, declaration.split(" static ", 1)[1], 1)
        with pytest.raises(AssertionError):
            _assert_lifetime_protocol(changed)
    # Explicitly reverse stop/relocation and verification/release ordering.
    for first, second in (("if (pcc_stop_the_world() != 0) return 90;", "result = relocate_probe(backend);"),
                          ("result = verify_live_copy();", "release_probe();")):
        changed = probe.PROBE_SOURCE.replace(first, "@SWAP@", 1).replace(second, first, 1).replace("@SWAP@", second, 1)
        with pytest.raises(AssertionError):
            _assert_lifetime_protocol(changed)


def test_initial_snapshot_is_locked_stable_and_printed_before_mutating_observation():
    _assert_candidate_snapshot(probe.PROBE_SOURCE)
    body = _diagnostic()
    first, secondary = body.split("    /* This secondary predicate", 1)
    calls = re.findall(r"\b([a-z_]\w*)\s*\(", re.sub(r"/\*.*?\*/", "", first, flags=re.S))
    assert calls == [
        "fail_state", "pcc_py_gc_minor_graph_lock", "pcc_gc_alloc_stage_pending",
        "pcc_gc_relocation_set_contains", "pcc_gc_relocation_set_size",
        "pcc_gc_backend4_evacuation_candidate_score",
        "pcc_gc_backend4_evacuation_candidate_bytes",
        "pcc_gc_object_index_find_stable", "pcc_gc_object_node_freeing",
        "pcc_gc_object_node_size", "pcc_gc_forwarding_find",
        "pcc_gc_forwarding_target_exists", "flags_of", "py_header",
        "pcc_gc_alloc_stage_pending", "pcc_py_gc_minor_graph_unlock", "fprintf",
    ]
    assert "node ? pcc_gc_object_node_freeing(node) : -1" in first
    assert "node ? pcc_gc_object_node_size(node) : 0" in first
    assert '"relocation_context=post-failure-stable-index code=%d "' in first
    assert "NOT the first rejected copy phase" in first
    assert "requested_size=%lld pending_after=%lld" in first
    assert "pcc_gc_object_known_size" not in body
    assert "pcc_gc_object_index_find(" not in body
    assert "pcc_gc_relocate_copy(" not in body
    assert "pcc_gc_select_relocation_set(" not in body
    assert "pcc_gc_relocation_member_count" not in body
    assert secondary.index("pcc_py_gc_minor_graph_lock()") < secondary.index(
        "pin_pending_before = pcc_gc_alloc_stage_pending()"
    ) < secondary.index("pcc_gc_object_is_address_pinned(source)") < secondary.index(
        "pcc_gc_object_index_find_stable(source)"
    ) < secondary.index("pin_pending_after = pcc_gc_alloc_stage_pending()") < secondary.index(
        "pcc_py_gc_minor_graph_unlock()"
    ) < secondary.index("fprintf(")
    assert "post-failure-secondary-may-drain" in secondary
    assert secondary.rstrip().endswith("return result;")

    # Extend the existing node rather than changing the fixed test inventory.
    mutations = (
        ("int64_t candidate_count = pcc_gc_backend4_evacuation_candidate_score();",
         "int64_t candidate_count = 1;"),
        ("int64_t candidate_bytes = pcc_gc_backend4_evacuation_candidate_bytes();",
         "int64_t candidate_bytes = 32;"),
        ("candidate_count = pcc_gc_backend4_evacuation_candidate_score()",
         "candidate_count = pcc_gc_backend4_evacuation_candidate_bytes()"),
        ("candidate_bytes = pcc_gc_backend4_evacuation_candidate_bytes()",
         "candidate_bytes = pcc_gc_backend4_evacuation_candidate_score()"),
        ("    int64_t candidate_count = pcc_gc_backend4_evacuation_candidate_score();",
         "    if (0) { int64_t candidate_count = pcc_gc_backend4_evacuation_candidate_score(); }"),
        ("    int64_t candidate_bytes = pcc_gc_backend4_evacuation_candidate_bytes();",
         "    if (0) { int64_t candidate_bytes = pcc_gc_backend4_evacuation_candidate_bytes(); }"),
        ("    pcc_py_gc_minor_graph_lock();", "    return result;\n    pcc_py_gc_minor_graph_lock();"),
        ("int result = fail_state(code, what, source, copy);", "int result = code;"),
        ("candidate_count=%lld candidate_bytes=%lld", "candidate_count=%d candidate_bytes=%d"),
        ("(long long)candidate_count,", "(long long)selection_size,"),
        ("(long long)candidate_bytes,", "(long long)published_size,"),
        ("(long long)candidate_bytes,", "(long long)pcc_gc_backend4_evacuation_candidate_bytes(),"),
    )
    for original, replacement in mutations:
        assert original in probe.PROBE_SOURCE
        changed = probe.PROBE_SOURCE.replace(original, replacement, 1)
        with pytest.raises(AssertionError):
            _assert_candidate_snapshot(changed)
    for symbol, variable in ((_CANDIDATE_GETTERS[0][0], "candidate_count"),
                             (_CANDIDATE_GETTERS[1][0], "candidate_bytes")):
        declaration = f"    int64_t {variable} = {symbol}();\n"
        assert probe.PROBE_SOURCE.count(declaration) == 1
        changed = probe.PROBE_SOURCE.replace(declaration, "", 1).replace(
            "    pcc_py_gc_minor_graph_unlock();\n",
            "    pcc_py_gc_minor_graph_unlock();\n" + declaration, 1,
        )
        with pytest.raises(AssertionError):
            _assert_candidate_snapshot(changed)


@pytest.mark.parametrize(("module", "symbol", "arguments", "returns"), [
    ("freestanding_gc_alloc_stage", "pcc_gc_alloc_stage_pending", [], "i64"),
    ("freestanding_gc_object_nodes", "pcc_gc_object_node_freeing", ["c_ptr"], "i64"),
    ("freestanding_gc_object_nodes", "pcc_gc_object_node_size", ["c_ptr"], "i64"),
    ("freestanding_gc_forwarding_identity", "pcc_gc_forwarding_find", ["c_ptr"], "c_ptr"),
    ("freestanding_gc_forwarding_identity", "pcc_gc_forwarding_target_exists", ["c_ptr"], "i64"),
    ("freestanding_gc_root_operations", "pcc_gc_object_is_address_pinned", [None], "i64"),
    ("freestanding_gc_index_table", "pcc_gc_object_index_find_stable", ["c_ptr"], "c_ptr"),
    ("freestanding_runtime_high_substrate", "pcc_py_gc_minor_graph_lock", [], "None"),
    ("freestanding_runtime_high_substrate", "pcc_py_gc_minor_graph_unlock", [], "None"),
    ("py_gc_backend", "pcc_gc_relocation_set_contains", [None], "int"),
    ("py_gc_backend", "pcc_gc_relocation_set_size", [], "int"),
])
def test_snapshot_calls_use_existing_production_c_abi(module, symbol, arguments, returns):
    tree = ast.parse((RUNTIME / (module + ".py")).read_text())
    exported = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and any(isinstance(mark, ast.Call)
                        and isinstance(mark.func, ast.Name) and mark.func.id == "c_abi_export"
                        and mark.args and ast.literal_eval(mark.args[0]) == symbol
                        for mark in node.decorator_list)]
    assert len(exported) == 1
    function = exported[0]
    assert [ast.unparse(arg.annotation) if arg.annotation else None
            for arg in function.args.args] == arguments
    assert ast.unparse(function.returns) == returns
    internal = (ROOT / "pcc/runtime/src/py_internal.h").read_text()
    public = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    declarations = {
        "pcc_gc_alloc_stage_pending": "int64_t pcc_gc_alloc_stage_pending(void);",
        "pcc_gc_object_node_freeing": "int64_t pcc_gc_object_node_freeing(void *);",
        "pcc_gc_object_node_size": "int64_t pcc_gc_object_node_size(void *);",
        "pcc_gc_forwarding_find": "void *pcc_gc_forwarding_find(PyObject *);",
        "pcc_gc_forwarding_target_exists": "int64_t pcc_gc_forwarding_target_exists(PyObject *);",
        "pcc_gc_object_is_address_pinned": "int64_t pcc_gc_object_is_address_pinned(PyObject *);",
        "pcc_gc_object_index_find_stable": "void *pcc_gc_object_index_find_stable(PyObject *obj);",
        "pcc_py_gc_minor_graph_lock": "void pcc_py_gc_minor_graph_lock(void);",
        "pcc_py_gc_minor_graph_unlock": "void pcc_py_gc_minor_graph_unlock(void);",
        "pcc_gc_relocation_set_contains": "int64_t pcc_gc_relocation_set_contains(PyObject *o);",
        "pcc_gc_relocation_set_size": "int64_t pcc_gc_relocation_set_size(void);",
    }
    assert declarations[symbol] in " ".join((probe.PROBE_SOURCE + internal + public).split())

    if symbol == "pcc_gc_relocation_set_size":
        runtime = (RUNTIME / "py_gc_backend.py").read_text()
        assert '#include "../include/py_runtime.h"' in internal
        _assert_candidate_getters(runtime, public)
        for getter, counter in _CANDIDATE_GETTERS:
            exported = f'@c_abi_export("{getter}")'
            loaded = f'return load_i32(global_addr("{counter}"), 0)'
            assert runtime.count(exported) == runtime.count(loaded) == 1
            for original, replacement in (
                (exported, ""),
                (loaded, loaded.replace(counter, "pcc_gc_backend4_copied_objects")),
                (loaded, 'pcc_gc_select_relocation_set(1)\n    ' + loaded),
                (loaded, 'pcc_gc_alloc(32)\n    ' + loaded),
            ):
                with pytest.raises(AssertionError):
                    _assert_candidate_getters(runtime.replace(original, replacement, 1), public)
            with pytest.raises(AssertionError):
                _assert_candidate_getters(runtime, public.replace(getter + "(void)", getter + "(int32_t)", 1))


@pytest.mark.parametrize("target", ["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"])
def test_complete_probe_lowers_with_owned_headers_without_linking(tmp_path, monkeypatch, target):
    from pcc.backend.self_backend_parse import decode_llvm_c_string
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator, TranslationUnit

    def forbidden(*args, **kwargs):
        raise AssertionError("host diagnostic contract must not build/link/run a runtime or use a tool")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(probe, "admitted_runtime", forbidden)
    monkeypatch.setattr(CEvaluator, "emit_executable", forbidden)
    source = probe.PROBE_SOURCE.replace("@BACKEND@", "4").replace(
        "@EXPECTED_THREADS@", "1"
    ).replace("@EXPECTED_REFCOUNT_STRATEGY@", "PCC_REFCOUNT_STRATEGY_ATOMIC")
    evaluator = CEvaluator(backend="self", target_triple=target)
    units = evaluator.compile_translation_units(
        [TranslationUnit("probe.c", str(tmp_path / "probe.c"), source)],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(ROOT / "pcc/runtime/include"), str(ROOT / "pcc/runtime/src"),
                      str(ROOT / "utils/fake_libc_include")],
    )
    assert len(units) == 1
    ir = units[0][1]
    (tmp_path / "probe.ll").write_text(ir)
    for symbol in ("fail_relocation_state", "pcc_gc_alloc_stage_pending",
                   "pcc_gc_object_index_find_stable", "pcc_gc_object_is_address_pinned"):
        assert symbol in ir
    for symbol, _ in _CANDIDATE_GETTERS:
        # Require actual lowered calls, not just declarations or strings.
        assert re.search(r'\bcall\s+i64\s+@' + re.escape(symbol) + r'\(\)', ir)
    literals = [decode_llvm_c_string(token) for token in re.findall(r'c"[^"\n]*"', ir)]
    assert (
        b"relocation_context=post-failure-stable-index code=%d "
        b"pending_before=%lld selected=%lld selection_size=%lld "
        b"candidate_count=%lld candidate_bytes=%lld "
        b"published_node=%d node_freeing=%lld published_node_size=%lld "
        b"forwarding_record=%p source_is_forwarding_target=%lld "
        b"src_flags=0x%x header_pinned=%d src_tag=%d "
        b"requested_size=%lld pending_after=%lld\n\0"
    ) in literals
    assert (
        b"relocation_context=post-failure-secondary-may-drain code=%d "
        b"pending_before=%lld address_pinned=%lld published_node=%d "
        b"pending_after=%lld\n\0"
    ) in literals


@pytest.mark.parametrize("code", [13, 14])
def test_failure_receipt_preserves_code_stderr_source_and_native_marker(tmp_path, monkeypatch, code):
    from scripts import ci_worker_size_prior_gate as gate

    archive = tmp_path / "host-fixture-not-a-runtime.a"
    archive.write_bytes(b"synthetic failed-run fixture, not native execution")
    Path(str(archive) + ".capi_syms").write_text("synthetic symbol inventory")
    Path(str(archive) + ".provenance.json").write_text(json.dumps({
        "target_triple": "host-fixture-only",
        "members": [{"runtime_build_config": {"threads": True, "refcount": "atomic"}}],
    }))
    monkeypatch.setattr(probe, "admitted_runtime", lambda: archive)
    linked = []

    def capture_link(source, executable, **kwargs):
        linked.append(source)
        executable.write_bytes(b"synthetic non-executable output")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    stderr = (f"code={code} what=synthetic-failure src_flags=0x40400\n"
              f"relocation_context=post-failure-stable-index code={code} pending_before=1 "
              f"candidate_count=1 candidate_bytes=32\n"
              f"relocation_context=post-failure-secondary-may-drain code={code} pending_after=0\n")
    observed = []

    def failed_run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(returncode=code, stdout="", stderr=stderr)

    monkeypatch.setattr(probe, "link_c_harness", capture_link)
    monkeypatch.setattr(probe, "run_process_group_timeout", failed_run)
    with pytest.raises(AssertionError) as raised:
        probe.test_relocation_copy_clears_stale_sweep_verdict_backend4_single_mutator(tmp_path)
    # Pytest's assertion rewriting indents message continuation lines and adds
    # an expression explanation. The machine-readable receipt stays exact.
    for line in stderr.splitlines():
        assert line in str(raised.value)
    assert f"probe returned {code}:" in str(raised.value)
    assert len(linked) == len(observed) == 1
    source = linked[0]
    receipt = json.loads(source.with_suffix(".run.json").read_text())
    assert receipt["returncode"] == code and receipt["stderr"] == stderr
    assert receipt["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["compiler"] == "host-pcc0" and receipt["backend"] == "self"
    assert receipt["runtime_provisioning"] is False
    assert observed[0][1]["timeout"] == 120
    assert "PCC_REFCOUNT_STRATEGY" not in observed[0][1]["env"]
    function = probe.test_relocation_copy_clears_stale_sweep_verdict_backend4_single_mutator
    assert [mark.name for mark in function.pytestmark] == ["integration"]
    node = "tests/python/test_gc_relocation_sweep_verdict.py::" + function.__name__
    command = gate.pytest_command(tmp_path, [node], integration=True)
    assert command[-3:] == ["-m", "integration", node]
    assert "-x" in command
    assert not any(arg in ("-n", "-n0", "--dist", "-o", "--override-ini") for arg in command)

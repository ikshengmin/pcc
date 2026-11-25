"""No-op peepholes publish independent roots without duplicating their trees.

Sequence mutation/identity follows Python Reference 6.2.7 and 6.3; exception
cleanup follows 8.4. Scalar-node counts are structural checks, not time or RSS.
"""

import ast
from pathlib import Path

import pytest

from pcc.backend import arm64_encode as enc
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend.self_backend_aarch64_fragments import (
    AArch64EmissionFragments, EMISSION_RECORD_EMPTY, EMISSION_RECORD_DIRECTIVE,
    EMISSION_DIRECTIVE_TEXT_SECTION,
)
from pcc.backend.self_backend_value_arena import CompilerInt4
from tests.python.owned_regression_support import (
    assert_owned_program, explicit_owned_runtime,
)
from tests.python.test_aarch64_typed_peepholes import CASES, A, I, R, _fixture, _ids


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/backend/self_backend_aarch64_typed_peepholes.py"


# Frozen original traversal/match/publication control flow. The unchanged
# _Pass helpers are the same production helpers in both arms.
_LEGACY_RUN_SOURCE = '''
def _legacy_run(fragments, semantics, span, mode):
    p = _Pass(fragments, semantics, span)
    try:
        index = 0
        function_end = 0
        in_function = False
        loaded = []
        while index < len(p.ids):
            a, b, c, d = (p.rid(index + n) for n in range(4))
            replacement = -2
            consumed = 0
            if mode in (1, 2):
                last = b if mode == 1 else c
                if last >= 0:
                    middle = None
                    if mode == 2:
                        middle = p.stack(b, "ldur") or p.stack(b, "ldurb")
                    for store_op, load_op in (("stur", "ldur"), ("sturb", "ldurb")):
                        store, load = p.stack(a, store_op), p.stack(last, load_op)
                        if store is None or load is None or not _same(store[1], load[1]):
                            continue
                        if mode == 2 and (middle is None or _same(middle[1], store[1]) or _forward_alias(middle[0]) == _forward_alias(store[0])):
                            continue
                        replacement = p.new_move(load[0], store[0], load_op == "ldurb")
                        if replacement != -2:
                            p.output.append(a)
                            if mode == 2:
                                p.output.append(b)
                            consumed = mode + 1
                            break
            elif mode in (3, 4) and b >= 0:
                zero = p.zero(a)
                move = p.move(a)
                old = zero if mode == 3 else (move[0] if move else -1)
                if old >= 0 and _scratch(old) and p.op(b) in ("stur", "str", "sturb", "strb") and p.register(b, 0) == old and p.dead(index + 2, old):
                    new = (sem.REGISTER_WZR if _class(old) == "w" else sem.REGISTER_XZR) if mode == 3 else move[1]
                    replacement = p.changed_register(b, 0, new, 0)
                    consumed = 2
            elif mode in (5, 6) and b >= 0:
                compare = p.compare(b)
                zero = p.zero(a)
                move = p.move(a)
                if compare is not None:
                    lhs, rhs = compare
                    if mode == 5 and zero >= 0 and rhs == zero and lhs != zero and p.dead(index + 2, zero):
                        replacement = p.new("cmp", [_reg(lhs), CompilerInt4(sem.TOKEN_IMMEDIATE, 0, 10, sem.IMMEDIATE_HASH)], encode_emitted_compare_immediate_parts(_spelling(lhs), 0))
                        consumed = 2
                    if mode == 6 and move and _scratch(move[0]) and move[0] in (lhs, rhs) and p.dead(index + 2, move[0]):
                        lhs = move[1] if lhs == move[0] else lhs
                        rhs = move[1] if rhs == move[0] else rhs
                        replacement = p.new("cmp", [_reg(lhs), _reg(rhs)], encode_emitted_compare_register_parts(_spelling(lhs), _spelling(rhs)))
                        consumed = 2
            elif mode == 7 and b >= 0:
                move, branch = p.move(a), p.branch(b)
                if move and branch and move[0] == branch[1] and _class(move[0]) == "w" and _class(move[1]) == "w" and _scratch(move[0]) and p.false_path(index + 2):
                    replacement = p.new_branch(branch[0], branch[2], move[1])
                    consumed = 2
            elif mode == 8 and b >= 0:
                move = p.move(a)
                t = p.tokens(b)
                dest, lhs = p.register(b, 0), p.register(b, 1)
                if (move and p.op(b) in ("add", "sub") and len(t) >= 3
                    and dest >= 0 and lhs >= 0 and _class(dest) in ("w", "x")
                    and _class(dest) == _class(lhs) and dest == lhs == move[0]
                    and _scratch(dest) and not any(p.token_uses(token, dest) for token in t[2:])):
                    replacement = p.changed_register(b, 1, move[1], 5)
                    consumed = 2
            elif mode == 9:
                move = p.move(a)
                if move and len(p.output) and _scratch(move[1]) and _spelling(move[0])[1:] not in ("sp", "zr") and p.dead(index + 1, move[1], True):
                    previous = p.output.get_unchecked(len(p.output) - 1)
                    t = p.tokens(previous)
                    if (p.op(previous) in _RENAMEABLE and len(t) >= 2
                        and p.register(previous, 0) == move[1]
                        and not any(token.first == sem.TOKEN_ADDRESS and token.second in (sem.ADDRESS_PREINDEX, sem.ADDRESS_POSTINDEX) for token in t[1:])):
                        p.output.truncate(len(p.output) - 1)
                        if p.op(previous) == "mov" and len(t) == 2 and p.register(previous, 1) == move[0]:
                            replacement = -1
                        else:
                            replacement = p.changed_register(previous, 0, move[0], 0)
                        consumed = 1
            elif mode == 10 and b >= 0:
                first, second = p.move(a), p.move(b)
                if first and second and first[0] == second[1] and _scratch(first[0]) and p.dead(index + 2, first[0], True):
                    replacement = p.new_move(second[0], first[1])
                    if replacement != -2:
                        consumed = 2
            elif mode == 11 and b >= 0:
                zero, branch = p.zero(a), p.branch(b)
                if zero >= 0 and branch and branch[1] == zero:
                    replacement = p.new_branch("b", branch[2]) if branch[0] == "cbz" else a
                    consumed = 2
            elif mode == 12 and d >= 0:
                cset, store, branch = p.cset(a), p.stack(b, "sturb"), p.branch(d)
                t = p.tokens(c)
                dst, src = p.register(c, 0), p.register(c, 1)
                if (cset and store and branch and p.op(c) == "and" and len(t) == 3
                    and dst >= 0 and src >= 0 and _class(dst) == _class(src) == "w"
                    and _same(t[2], CompilerInt4(sem.TOKEN_IMMEDIATE, 255, 16, sem.IMMEDIATE_HASH))
                    and store[0] == src == cset[0] and branch[1] == dst):
                    p.output.append(a)
                    p.output.append(b)
                    replacement = p.new_branch(branch[0], branch[2], cset[0])
                    consumed = 4
            elif mode == 13 and d >= 0 and p.op(a) in ("cmp", "fcmp"):
                cset, store, branch = p.cset(b), p.stack(c, "sturb"), p.branch(d)
                if cset and store and branch:
                    cond = cset[1] if branch[0] == "cbnz" else _INVERSE.get(cset[1], "")
                    if cond and store[0] == branch[1] == cset[0]:
                        p.output.append(a)
                        p.output.append(b)
                        p.output.append(c)
                        replacement = p.new_branch("b." + cond, branch[2])
                        consumed = 4
            elif mode == 14:
                # The original pass preserves the prefix before the first
                # function and partitions at function labels, not sections.
                if p.function_label(a):
                    in_function = True
                    function_end = index + 1
                    while function_end < len(p.ids) and not p.function_label(p.rid(function_end)):
                        function_end += 1
                    loaded = []
                    for j in range(index, function_end):
                        load = p.stack(p.rid(j), "ldurb")
                        if load:
                            loaded.append(load[1])
                if in_function and index + 3 < function_end and p.op(a) in ("cmp", "fcmp"):
                    cset, store = p.cset(b), p.stack(c, "sturb")
                    if cset and store and p.op(d).startswith("b.") and store[0] == cset[0] and not any(_same(store[1], offset) for offset in loaded):
                        p.output.append(a)
                        replacement = d
                        consumed = 4
            if consumed:
                if replacement >= 0:
                    p.output.append(replacement)
                index += consumed
            else:
                p.output.append(a)
                index += 1
        return p.finish()
    finally:
        p.close()
'''


def _legacy_run():
    namespace = dict(vars(typed))
    exec(compile(_LEGACY_RUN_SOURCE, "<frozen pre-snapshot peephole>", "exec"), namespace)
    return namespace["_legacy_run"]


def _corrected_legacy_run():
    # Historical body plus individually reviewed safety corrections. This
    # never copies the current matcher to generate its expected control flow.
    from tests.python.frozen_safety_deltas import corrected_frozen
    function = corrected_frozen(_LEGACY_RUN_SOURCE)
    namespace = dict(vars(typed))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 "<reviewed frozen safety corrections>", "exec"), namespace)
    return namespace["_legacy_run"]


def _nop_owner(count, shape="plain"):
    owner = AArch64EmissionFragments(capture_semantics=True)
    assert owner.semantics is not None
    source = owner.new_fragment()
    if count:
        if shape == "mixed":
            sem.append_semantic_event(owner, owner.semantics, source, CompilerInt4(
                EMISSION_DIRECTIVE_TEXT_SECTION, EMISSION_RECORD_DIRECTIVE, 0, -1,
            ))
            owner.append_memory_pair_barrier(source, True)
        owner.append_label(source, "_noop")
        for _ in range(count):
            owner.append_nop(source)
        if shape == "mixed":
            sem.append_semantic_event(owner, owner.semantics, source, CompilerInt4(
                0, EMISSION_RECORD_EMPTY, 0, -1,
            ))
            owner.append_memory_pair_barrier(source, False)
        owner.append_label(source, "L_end")
    if shape == "shared":
        shared = owner.new_fragment()
        owner.extend_fragment(shared, source)
        owner.extend_fragment(shared, shared)
        source = shared
    return owner, owner.semantics, source


@pytest.mark.parametrize("opcode", ["stur", "sturb", "ldur", "ldurb"])
def test_nonmatching_stack_opcode_validates_without_operand_snapshot(monkeypatch, opcode):
    owner = AArch64EmissionFragments(capture_semantics=True)
    semantics = owner.semantics
    source = owner.new_fragment()
    owner.append_label(source, "_f")
    sem.append_semantic_event(owner, semantics, source, CompilerInt4(
        0, EMISSION_RECORD_EMPTY, 0, -1,
    ))
    owner.append_move(source, "x9", "x10")
    matcher = typed._Pass(owner, semantics, source)
    try:
        label, empty, rid = _ids(owner, source)
        reads = []
        require_header = semantics.require_header

        def validated(record_id):
            reads.append(record_id)
            return require_header(record_id)

        def forbidden_snapshot(_record_id):
            raise AssertionError("nonmatching opcode copied operand values")

        monkeypatch.setattr(semantics, "require_header", validated)
        monkeypatch.setattr(matcher, "tokens", forbidden_snapshot)
        assert matcher.stack(rid, opcode) is None
        assert reads == [rid]
        assert matcher.stack(label, opcode) is None
        assert matcher.stack(empty, opcode) is None
        assert matcher.stack(-1, opcode) is None
        assert reads == [rid]
        # A mismatch is not authority to skip the current operand validation.
        semantics.tokens.set(1, 999)
        with pytest.raises(enc.EncodeError):
            matcher.stack(rid, opcode)
        assert reads == [rid, rid]
        semantics.close()
        with pytest.raises(RuntimeError, match="closed"):
            matcher.stack(rid, opcode)
    finally:
        matcher.close()
        semantics.close()
        owner.close()


@pytest.mark.parametrize("opcode,register", [
    ("stur", "x9"), ("sturb", "w9"), ("ldur", "x9"), ("ldurb", "w9"),
])
def test_matching_stack_opcode_keeps_fresh_operand_snapshot(monkeypatch, opcode, register):
    owner = AArch64EmissionFragments(capture_semantics=True)
    semantics = owner.semantics
    source = owner.new_fragment()
    owner.append_memory(source, opcode, register, "x29", -8)
    matcher = typed._Pass(owner, semantics, source)
    try:
        rid = _ids(owner, source)[0]
        snapshots = []
        original = matcher.tokens

        def snapshot(record_id):
            snapshots.append(record_id)
            return original(record_id)

        monkeypatch.setattr(matcher, "tokens", snapshot)
        expected_offset = sem.semantic_immediate_token(-8)
        assert matcher.stack(rid, opcode) == (sem.semantic_register_id(register), expected_offset)
        assert snapshots == [rid]
        changed_register = register[0] + "13"
        semantics.tokens.set(1, sem.semantic_register_id(changed_register))
        assert matcher.stack(rid, opcode) == (sem.semantic_register_id(changed_register), expected_offset)
        assert snapshots == [rid, rid]
        semantics.tokens.set(8, sem.TOKEN_IMMEDIATE)
        with pytest.raises(enc.EncodeError):
            matcher.stack(rid, opcode)
    finally:
        matcher.close()
        semantics.close()
        owner.close()


def test_frozen_oracle_is_exact_except_for_noop_publication():
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run")

    class StripSnapshot(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any(isinstance(target, ast.Name) and target.id == "changed" for target in node.targets):
                assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool)
                return None
            return self.generic_visit(node)

        def visit_If(self, node):
            if (isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.And)
                    and ast.unparse(node.test.values[0]) == "not changed"):
                assert ast.unparse(node.test.values[1]) == "fragments.spans.length(span) == len(p.ids)"
                assert ast.unparse(node.body[0]) == "result = fragments.new_fragment()"
                assert ast.unparse(node.body[1]) == "fragments.extend_fragment(result, span)"
                assert ast.unparse(node.body[2]) == "return result"
                return None
            return self.generic_visit(node)

    # Keep the original fixture and projection below available unchanged.
    # Its full-matcher assumption predates independent safety fixes. Bind the
    # default route to the real runtime function, then compare every remaining
    # statement with the explicitly corrected historical expectation.
    from tests.python.default_oracle_specialization import assert_frozen_default
    assert_frozen_default(
        SOURCE.read_text(), Path(__file__).read_text(), corrected=True,
        runtime_function=typed._run,
    )


@pytest.mark.parametrize("mode", range(1, 15))
@pytest.mark.parametrize("count", (0, 1, 16, 256))
@pytest.mark.parametrize("shape", ("plain", "mixed", "shared"))
def test_each_noop_pass_keeps_fresh_handle_without_node_copies(mode, count, shape):
    outcomes = []
    for run, snapshot in ((_legacy_run(), False), (typed._run, True)):
        owner, semantics, source = _nop_owner(count, shape)
        try:
            before = _ids(owner, source)
            nodes = len(owner.spans.nodes)
            runs = len(owner.spans.runs)
            headers = len(owner.spans.spans)
            records = len(owner.records)
            result = run(owner, semantics, source, mode)
            assert result.first != source.first and result.second == source.second
            assert _ids(owner, source) == _ids(owner, result) == before
            assert len(owner.records) == records
            assert len(owner.spans.spans) == headers + 4  # staging root and result root
            # A rebuild is one run node over a copy of every record ID; a
            # no-op snapshot copies neither.
            assert len(owner.spans.nodes) - nodes == (0 if snapshot or not before else 3)
            assert len(owner.spans.runs) - runs == (0 if snapshot else len(before))
            assert owner.spans.projection_count == 0
            semantics.require_complete(owner.records, len(owner.symbol_names))
            outcomes.append(before)
        finally:
            owner.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("mode", range(1, 15))
def test_noop_result_keeps_independent_appends_self_extension_and_cursor(mode):
    outcomes = []
    for run in (_legacy_run(), typed._run):
        owner, semantics, source = _nop_owner(4)
        try:
            before = _ids(owner, source)
            result = run(owner, semantics, source, mode)
            saved = owner.new_fragment()
            owner.extend_fragment(saved, result)
            owner.start_cursor(result)
            owner.append_nop(source)
            owner.append_nop(result)
            # A cursor snapshots the old root even while both public roots grow.
            cursor_values = []
            record_id = owner.next_record_id()
            while record_id >= 0:
                cursor_values.append(record_id)
                record_id = owner.next_record_id()
            assert cursor_values == before
            assert _ids(owner, saved) == before
            source_ids, result_ids = _ids(owner, source), _ids(owner, result)
            assert source_ids[:-1] == result_ids[:-1] == before
            assert source_ids[-1] != result_ids[-1]
            owner.extend_fragment(result, result)
            assert _ids(owner, result) == result_ids * 2
            assert _ids(owner, source) == source_ids
            assert _ids(owner, saved) == before
            semantics.require_complete(owner.records, len(owner.symbol_names))
            outcomes.append((source_ids, result_ids, cursor_values))
        finally:
            owner.close()
    assert outcomes[0] == outcomes[1]


_REWRITES = list(enumerate(CASES, 1)) + [
    (1, ("delete-forwarded-load", [I("stur", R("x9"), A()), I("ldur", R("x9"), A())])),
    (9, ("truncate-and-delete", [I("mov", R("x9"), R("x19")), I("mov", R("x19"), R("x9"))])),
    (10, ("delete-move-chain", [I("mov", R("x9"), R("x19")), I("mov", R("x19"), R("x9"))])),
]


@pytest.mark.parametrize("mode,case", _REWRITES, ids=[case[0] for _, case in _REWRITES])
def test_every_real_rewrite_keeps_original_publication_bytes_and_ids(mode, case):
    _name, items = case
    outcomes = []
    for run in (_corrected_legacy_run(), typed._run):
        owner, semantics, source, _lines = _fixture(items)
        try:
            before = _ids(owner, source)
            result = run(owner, semantics, source, mode)
            after = _ids(owner, result)
            # Mode14 deliberately retains the CSET and stack store; removing
            # either is killed by the independent register/memory regressions.
            assert (after != before) is (mode != 14)
            assert _ids(owner, source) == before
            semantics.require_complete(owner.records, len(owner.symbol_names))
            outcomes.append((
                after,
                [owner.records.get_unchecked(index) for index in range(len(owner.records))],
                [semantics.headers.get_unchecked(index) for index in range(len(semantics.headers))],
                [semantics.tokens.get_unchecked(index) for index in range(len(semantics.tokens))],
                [owner.spans.nodes.get_unchecked(index) for index in range(len(owner.spans.nodes))],
                [owner.spans.spans.get_unchecked(index) for index in range(len(owner.spans.spans))],
            ))
        finally:
            semantics.close()
            owner.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("count", (1, 4, 16, 64))
def test_fourteen_noop_passes_do_not_retain_fourteen_record_trees(count):
    outcomes = []
    for snapshot in (False, True):
        owner, semantics, source = _nop_owner(count)
        try:
            before = _ids(owner, source)
            nodes = len(owner.spans.nodes)
            runs = len(owner.spans.runs)
            headers = len(owner.spans.spans)
            if snapshot:
                result = typed.run_aarch64_typed_peepholes(owner, semantics, source)
            else:
                semantics.require_complete(owner.records, len(owner.symbol_names))
                result = source
                run = _legacy_run()
                for mode in range(1, 15):
                    result = run(owner, semantics, result, mode)
            assert len(owner.spans.nodes) - nodes == (0 if snapshot or not before else 14 * 3)
            assert len(owner.spans.runs) - runs == (0 if snapshot else 14 * len(before))
            # Four span entries per noop pass: the frozen fourteen, plus the
            # driver's fifteenth pass, fold_sign_extending_load.
            assert len(owner.spans.spans) == headers + (60 if snapshot else 56)
            assert _ids(owner, result) == _ids(owner, source) == before
            assert owner.spans.projection_count == 0
            outcomes.append(before)
        finally:
            owner.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("mode", (1, 14))
@pytest.mark.parametrize("stored_length", (-1, 0, 1, 100, 0x80000000))
def test_corrupt_root_count_keeps_old_normalizing_rebuild(mode, stored_length):
    outcomes = []
    for run in (_legacy_run(), typed._run):
        owner, semantics, source = _nop_owner(4)
        try:
            before = _ids(owner, source)
            root = owner.spans.spans.get2_unchecked(source.first)
            assert stored_length != len(before)
            owner.spans.spans.set2_unchecked(source.first * 2, root.first, stored_length)
            nodes = len(owner.spans.nodes)
            runs = len(owner.spans.runs)
            result = run(owner, semantics, source, mode)
            assert _ids(owner, result) == before
            assert owner.spans.length(result) == len(before)
            assert owner.spans.length(source) == stored_length
            # The normalizing rebuild still copies every record ID, as one run.
            assert len(owner.spans.nodes) - nodes == 3
            assert len(owner.spans.runs) - runs == len(before)
            outcomes.append(_ids(owner, result))
        finally:
            owner.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("malformed", ("record", "leaf-length", "cycle", "header"))
def test_malformed_record_tree_and_provenance_keep_existing_failure(malformed):
    outcomes = []
    for run in (_legacy_run(), typed._run):
        owner, semantics, source = _nop_owner(1)
        try:
            if malformed == "record":
                owner.spans.append(source, len(owner.records) // 4)
            elif malformed == "leaf-length":
                owner.spans.nodes.set_unchecked(2, 2)
            elif malformed == "cycle":
                root = owner.spans.spans.get2_unchecked(source.first)
                owner.spans.nodes.set_unchecked(root.first * 3, root.first)
            else:
                # First record is a label, second is the instruction.
                semantics.headers.set_unchecked(4, 0)
            nodes, headers, records = len(owner.spans.nodes), len(owner.spans.spans), len(owner.records)
            with pytest.raises((enc.EncodeError, RuntimeError)) as caught:
                run(owner, semantics, source, 1)
            assert (len(owner.spans.nodes), len(owner.spans.spans), len(owner.records)) == (nodes, headers, records)
            outcomes.append((type(caught.value), str(caught.value)))
        finally:
            owner.close()
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("failure", ("staging-root", "result-root", "snapshot"))
def test_snapshot_publication_failure_keeps_input_and_closes_scratch(monkeypatch, failure):
    owner, semantics, source = _nop_owner(4)
    before = _ids(owner, source)
    nodes = len(owner.spans.nodes)
    error = MemoryError("injected publication allocation failure")
    real_arena = typed.CompilerIntArena
    scratch = []

    def arena():
        result = real_arena()
        scratch.append(result)
        return result

    real_root = owner.new_fragment
    calls = []

    def new_root():
        calls.append(None)
        if (failure == "staging-root" and len(calls) == 1) or (failure == "result-root" and len(calls) == 2):
            raise error
        return real_root()

    def bad_snapshot(destination, original):
        assert original == source
        raise error

    monkeypatch.setattr(typed, "CompilerIntArena", arena)
    monkeypatch.setattr(owner, "new_fragment", new_root)
    if failure == "snapshot":
        monkeypatch.setattr(owner, "extend_fragment", bad_snapshot)
    try:
        with pytest.raises(MemoryError) as caught:
            typed._run(owner, semantics, source, 1)
        assert caught.value is error
        assert len(scratch) == 2 and all(arena._closed for arena in scratch)
        assert _ids(owner, source) == before and len(owner.spans.nodes) == nodes
        semantics.require_complete(owner.records, len(owner.symbol_names))
    finally:
        owner.close()


PROGRAM = '''import sys
from pcc.backend.self_backend_aarch64_fragments import AArch64EmissionFragments
from pcc.backend.self_backend_aarch64_typed_peepholes import (
    run_aarch64_typed_peepholes, fold_mov_mov_chain, forward_adjacent_stack_store_load,
)
from pcc.backend.self_backend_value_arena import CompilerInt2, CompilerInt4

REFERENCE = len(sys.argv) == 2 and sys.argv[1] == "--reference"
if not REFERENCE:
    assert sys.implementation.name == "pcc"

def ids(owner: AArch64EmissionFragments, span: CompilerInt2) -> list[int]:
    values: list[int] = []
    owner.start_cursor(span)
    record_id = owner.next_record_id()
    while record_id >= 0:
        values.append(record_id)
        record_id = owner.next_record_id()
    return values

def noop(count: int) -> None:
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        semantics = owner.semantics
        assert semantics is not None
        if not REFERENCE:
            assert owner.records.uses_native_storage
            assert owner.cursor.uses_native_storage
            assert owner.spans.nodes.uses_native_storage
            assert owner.spans.spans.uses_native_storage
            assert semantics.headers.uses_native_storage
            assert semantics.tokens.uses_native_storage
        source: CompilerInt2 = owner.new_fragment()
        if count:
            owner.append_label(source, "_noop")
            for index in range(count):
                owner.append_nop(source)
        before = ids(owner, source)
        nodes = len(owner.spans.nodes)
        result: CompilerInt2 = run_aarch64_typed_peepholes(owner, semantics, source)
        assert result.first != source.first
        assert result.second == source.second
        assert ids(owner, result) == before
        assert len(owner.spans.nodes) == nodes
        saved: CompilerInt2 = owner.new_fragment()
        owner.extend_fragment(saved, result)
        owner.start_cursor(result)
        owner.append_nop(source)
        owner.append_nop(result)
        cursor_values: list[int] = []
        record_id = owner.next_record_id()
        while record_id >= 0:
            cursor_values.append(record_id)
            record_id = owner.next_record_id()
        assert cursor_values == before
        assert ids(owner, saved) == before
        source_ids = ids(owner, source)
        result_ids = ids(owner, result)
        assert source_ids[:-1] == before and result_ids[:-1] == before
        assert source_ids[-1] != result_ids[-1]
        owner.extend_fragment(result, result)
        assert ids(owner, result) == result_ids + result_ids
        assert ids(owner, source) == source_ids
        assert ids(owner, saved) == before
        assert owner.spans.projection_count == 0
        semantics.require_complete(owner.records, len(owner.symbol_names))
        print("snapshot", count, len(before), len(result_ids))
    finally:
        owner.close()

def rewrite() -> None:
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        semantics = owner.semantics
        assert semantics is not None
        source: CompilerInt2 = owner.new_fragment()
        owner.append_move(source, "x9", "x19")
        owner.append_move(source, "x20", "x9")
        before = ids(owner, source)
        result: CompilerInt2 = fold_mov_mov_chain(owner, semantics, source)
        selected = ids(owner, result)
        assert len(before) == 2 and len(selected) == 1
        assert ids(owner, source) == before
        assert owner.spans.projection_count == 0
        semantics.require_complete(owner.records, len(owner.symbol_names))
        record: CompilerInt4 = owner.records.get4_unchecked(selected[0])
        assert record.first == 0xAA1303F4  # mov x20, x19
        print("rewrite", selected[0], record.first)
    finally:
        owner.close()

def delete_load() -> None:
    owner = AArch64EmissionFragments(capture_semantics=True)
    try:
        semantics = owner.semantics
        assert semantics is not None
        source: CompilerInt2 = owner.new_fragment()
        owner.append_memory(source, "stur", "x9", "x29", -8)
        owner.append_memory(source, "ldur", "x9", "x29", -8)
        before = ids(owner, source)
        records = len(owner.records)
        result: CompilerInt2 = forward_adjacent_stack_store_load(owner, semantics, source)
        assert ids(owner, result) == [before[0]]
        assert ids(owner, source) == before and len(owner.records) == records
        assert owner.spans.projection_count == 0
        semantics.require_complete(owner.records, len(owner.symbol_names))
        print("delete", before[0])
    finally:
        owner.close()

def main() -> None:
    for count in (0, 1, 16, 64):
        noop(count)
    rewrite()
    delete_load()

main()
'''


@pytest.mark.integration
def test_compiled_real_peepholes_keep_native_snapshots_and_changed_output(
    tmp_path, monkeypatch, explicit_owned_runtime, python_program_compiler,
    request, capfd,
):
    # Compiler discovery uses PCC_PACKAGE_SITE, not the reference interpreter's
    # PYTHONPATH. Pin both to this checkout, never an installed PCC package.
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(ROOT))
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    expected = (
        "snapshot 0 0 1\n"
        "snapshot 1 2 3\n"
        "snapshot 16 17 18\n"
        "snapshot 64 65 66\n"
        f"rewrite 2 {0xAA1303F4}\n"
        "delete 0\n"
    )
    assert_owned_program(
        PROGRAM, expected, tmp_path, python_program_compiler,
        request.node.callspec.params["python_program_compiler"],
        explicit_owned_runtime, capfd, reference_arguments=("--reference",),
    )


# Proof/mutation obligations live in this already-inventoried CI module.
# No new lane, relaxed marker, altered budget or workflow is required.
from tests.python.default_oracle_specialization import _DefaultFalse, assert_frozen_default
FROZEN = Path(__file__)

def test_default_source_matches_reviewed_corrected_frozen_oracle():
    assert_frozen_default(SOURCE.read_text(), FROZEN.read_text(), corrected=True, runtime_function=typed._run)

def test_uncorrected_historical_matcher_is_not_a_full_default_oracle():
    with pytest.raises(AssertionError):
        assert_frozen_default(SOURCE.read_text(), FROZEN.read_text())

@pytest.mark.parametrize('old,new', [
    ('owned_matcher_only: bool = False', 'owned_matcher_only: bool = 0'),
    ('owned_matcher_only: bool = False', 'owned_matcher_only: bool = None'),
    ('    p = _Pass(fragments, semantics, span)\n    try:\n        changed', '    owned_matcher_only = True\n    p = _Pass(fragments, semantics, span)\n    try:\n        changed'),
    ('        if owned_matcher_only is not False:\n            sparse_positions', '        if owned_matcher_only != False:\n            sparse_positions'),
    ('        if owned_matcher_only is not False:\n            sparse_positions', '        observed()\n        if owned_matcher_only is not False:\n            sparse_positions'),
    ('elif owned_matcher_only is not False and mode == 14 and owned_matcher_only:', 'elif observed() and owned_matcher_only is not False and mode == 14 and owned_matcher_only:'),
])
def test_proof_rejects_default_path_mutants(old,new):
    text=SOURCE.read_text();assert old in text
    with pytest.raises(AssertionError):assert_frozen_default(text.replace(old,new,1),FROZEN.read_text(),corrected=True)

@pytest.mark.parametrize('expression', ['observed() and owned_matcher_only', 'observed() or not owned_matcher_only'])
def test_unknown_left_operand_is_retained(expression):
    node=_DefaultFalse().visit(ast.parse(expression,mode='eval').body)
    assert isinstance(node,ast.BoolOp)
    assert isinstance(node.values[0],ast.Call)
    assert node.values[0].func.id=='observed'

@pytest.mark.parametrize('old,new', [
    ('changed = False', 'changed = fragments.closed = True'),
    ('changed = False', 'changed = True'),
    ('        changed = False\n', ''),
    ('changed = True', 'changed = fragments.closed = True'),
    ('            return result\n        return p.finish()', '            return result\n        else:\n            observed()\n        return p.finish()'),
])
def test_snapshot_projection_rejects_extra_effects(old,new):
    text=SOURCE.read_text();assert old in text
    with pytest.raises(AssertionError):
        assert_frozen_default(text.replace(old,new,1),FROZEN.read_text(),corrected=True)

@pytest.mark.parametrize('suffix', ['\n_run = lambda *args, **kwargs: None\n', '\ndel _run\n', '\nfrom math import sin as _run\n'])
def test_source_rebinding_is_rejected(suffix):
    with pytest.raises(AssertionError):
        assert_frozen_default(SOURCE.read_text()+suffix,FROZEN.read_text(),corrected=True)

def test_runtime_rebinding_is_rejected():
    with pytest.raises(AssertionError):
        assert_frozen_default(SOURCE.read_text(),FROZEN.read_text(),corrected=True,runtime_function=lambda *args: None)

def test_forged_runtime_source_location_is_not_enough():
    from types import FunctionType
    code=(lambda *args: None).__code__.replace(co_filename=typed._run.__code__.co_filename,
                                             co_firstlineno=typed._run.__code__.co_firstlineno)
    forged=FunctionType(code,vars(typed),'_run',(False,))
    with pytest.raises(AssertionError):
        assert_frozen_default(SOURCE.read_text(),FROZEN.read_text(),corrected=True,runtime_function=forged)

@pytest.mark.parametrize('default',(0,None,True))
def test_runtime_default_must_be_exact_false(default):
    from types import FunctionType
    forged=FunctionType(typed._run.__code__,vars(typed),'_run',(default,))
    with pytest.raises(AssertionError):
        assert_frozen_default(SOURCE.read_text(),FROZEN.read_text(),corrected=True,runtime_function=forged)

from tests.python.frozen_safety_deltas import (
    EXPRESSIONS, STATEMENTS, MODE14_OLD, MODE14_NEW, _ExactNode, _ExactSequence,
    corrected_frozen,
)

REVERSED_DELTAS = [('expression', owner, old, new) for owner,old,new in EXPRESSIONS]
REVERSED_DELTAS += [('statements', owner, old, new) for owner,old,new in STATEMENTS]
REVERSED_DELTAS += [('statements','branch_live_registers-mode14',MODE14_OLD,MODE14_NEW)]

@pytest.mark.parametrize('kind,owner,old,new', REVERSED_DELTAS)
def test_each_safety_regression_is_rejected_by_whole_default_proof(kind,owner,old,new):
    tree=ast.parse(SOURCE.read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_run')
    if kind=='expression':
        edit=_ExactNode(ast.parse(new,mode='eval').body,ast.parse(old,mode='eval').body)
    else:
        edit=_ExactSequence(ast.parse(new).body,ast.parse(old).body)
    edit.visit(function)
    assert edit.count==1,owner
    with pytest.raises(AssertionError):
        assert_frozen_default(ast.unparse(tree),FROZEN.read_text(),corrected=True)


def test_corrected_fixture_requires_exact_historical_sites():
    frozen=ast.parse(FROZEN.read_text())
    historical=next(ast.literal_eval(n.value) for n in frozen.body if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='_LEGACY_RUN_SOURCE' for t in n.targets))
    old=EXPRESSIONS[0][1]
    function=ast.parse(historical).body[0]
    function.body.append(ast.Expr(value=ast.parse(old,mode='eval').body))
    with pytest.raises(AssertionError):corrected_frozen(ast.unparse(function))

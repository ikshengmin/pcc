"""Fixed semantic/error oracles and live-read tests for host token scalars."""
import pytest
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_value_arena as values
from pcc.backend.arm64_encode import EncodeError

REG = (sem.TOKEN_REGISTER, 1, 0, 0)
IMM = (sem.TOKEN_IMMEDIATE, 8, 10, sem.IMMEDIATE_HASH)
SHIFT = (sem.TOKEN_SHIFT, 0, 2, 1)
ADDRESS = (sem.TOKEN_ADDRESS, sem.ADDRESS_REGISTER, 3, 0)


def owner(rows, cls=sem.AArch64InstructionSemantics, arena_cls=values.CompilerIntArena):
    result = cls()
    if arena_cls is not values.CompilerIntArena:
        result.tokens.close()
        result.tokens = arena_cls()
    # Deliberately use raw fixture writes so malformed cases do not depend on
    # the producer validator being tested.
    for row in rows:
        result.tokens.append4(*row)
    return result


@pytest.mark.parametrize('rows', (
    [REG], [IMM],
    [(sem.TOKEN_ADDRESS, sem.ADDRESS_BASE, 1, 0), REG],
    [(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), REG, IMM],
    [(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), REG, (sem.TOKEN_SYMBOL, 0, 0, 0)],
    [ADDRESS, REG, REG, SHIFT],
))
def test_fixed_valid_fixtures(rows, monkeypatch):
    obj = owner(rows)
    calls = []
    original = sem.validate_semantic_token
    def validated(*parts):
        calls.append(parts)
        return original(*parts)
    monkeypatch.setattr(sem, 'validate_semantic_token', validated)
    try:
        obj._validate_tokens(0, len(rows))
        assert calls == rows
    finally:
        obj.close()


@pytest.mark.parametrize('rows, message', (
    ([(999, 0, 0, 0)], 'operand is invalid or unsupported'),
    ([ADDRESS, REG], 'address is truncated'),
    ([ADDRESS, IMM, REG, SHIFT], 'address base is not an X/SP register'),
    ([ADDRESS, (sem.TOKEN_REGISTER, 32, 0, 0), REG, SHIFT], 'address base is not an X/SP register'),
    ([ADDRESS, REG, IMM, SHIFT], 'address index is invalid'),
    ([(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), REG, SHIFT], 'address offset is invalid'),
    ([ADDRESS, REG, REG, IMM], 'address shift is invalid'),
    ([ADDRESS, (sem.TOKEN_REGISTER, 1, 99, 0), REG, SHIFT], 'operand is invalid or unsupported'),
))
def test_fixed_invalid_fixtures_and_priority(rows, message):
    obj = owner(rows)
    try:
        with pytest.raises(EncodeError, match=message):
            obj._validate_tokens(0, len(rows))
    finally:
        obj.close()


def test_host_address_reads_keep_exact_group_order(monkeypatch):
    obj = owner([ADDRESS, REG, REG, SHIFT])
    order = []
    original = sem.AArch64InstructionSemantics._host_token_parts
    def observed(self, index):
        order.append(index)
        return original(self, index)
    monkeypatch.setattr(sem.AArch64InstructionSemantics, '_host_token_parts', observed)
    try:
        obj._validate_tokens(0, 4)
        assert order == [0, 1, 2, 3, 1, 2, 3, 1, 2, 3]
    finally:
        obj.close()


@pytest.mark.parametrize('mutation', ('modify', 'truncate', 'replace', 'generic'))
def test_validator_mutation_is_seen_at_next_token_group(mutation, monkeypatch):
    obj = owner([REG, REG])
    retired = []
    calls = []
    original = sem.validate_semantic_token
    class Generic(values.CompilerIntArena):
        pass
    bad = (999, 0, 0, 0)
    def validated(*parts):
        calls.append(parts)
        original(*parts)
        if len(calls) != 1:
            return
        if mutation == 'modify':
            obj.tokens.set(4, 999)
        elif mutation == 'truncate':
            obj.tokens.truncate(4)
        else:
            replacement = Generic() if mutation == 'generic' else values.CompilerIntArena()
            replacement.append4(*REG)
            replacement.append4(*bad)
            retired.append(obj.tokens)
            obj.tokens = replacement
    monkeypatch.setattr(sem, 'validate_semantic_token', validated)
    try:
        expected_error = IndexError if mutation == 'truncate' else EncodeError
        with pytest.raises(expected_error):
            obj._validate_tokens(0, 2)
        expected_bad = (999, REG[1], REG[2], REG[3]) if mutation == 'modify' else bad
        assert calls == ([REG] if mutation == 'truncate' else [REG, expected_bad])
    finally:
        obj.close()
        for arena in retired:
            arena.close()


def test_custom_index_multiplication_replaces_storage_before_reads():
    obj = owner([REG])
    replacement = [sem.TOKEN_IMMEDIATE, 123, 10, 1]
    events = []
    class Index(int):
        def __mul__(self, count):
            events.append(count)
            obj.tokens._values = replacement
            return int(self) * count
    try:
        assert obj._host_token_parts(Index(0)) == tuple(replacement)
        assert events == [4]
    finally:
        obj.close()


@pytest.mark.parametrize('generic', ('arena', 'list', 'semantics'))
def test_generic_owners_keep_original_projection_and_read_order(generic, monkeypatch):
    class GenericArena(values.CompilerIntArena):
        pass
    class GenericSemantics(sem.AArch64InstructionSemantics):
        pass
    reads = []
    class GenericList(list):
        def __getitem__(self, index):
            reads.append(index)
            return super().__getitem__(index)
    obj = owner([ADDRESS, REG, REG, SHIFT],
                GenericSemantics if generic == 'semantics' else sem.AArch64InstructionSemantics,
                GenericArena if generic == 'arena' else values.CompilerIntArena)
    if generic == 'list':
        obj.tokens._values = GenericList(obj.tokens._values)
    projected = []
    original = values.CompilerIntArena.get4_unchecked
    def projection(self, index):
        if self is obj.tokens:
            projected.append(index)
        return original(self, index)
    monkeypatch.setattr(values.CompilerIntArena, 'get4_unchecked', projection)
    try:
        obj._validate_tokens(0, 4)
        expected = [0, 1, 2, 3, 1, 2, 3, 1, 2, 3]
        assert projected == expected
        if generic == 'list':
            assert reads == [4 * index + offset for index in expected for offset in range(4)]
    finally:
        obj.close()


def test_live_child_read_can_reject_after_valid_base_and_offset():
    class ChangingArena(values.CompilerIntArena):
        def get4_unchecked(self, index):
            token = super().get4_unchecked(index)
            # Shift is first read after base and offset; change the later
            # child read, not the values already returned to the validator.
            if index == 3:
                self.set(4, sem.TOKEN_CONDITION)
            return token
    obj = owner([ADDRESS, REG, REG, SHIFT], arena_cls=ChangingArena)
    try:
        with pytest.raises(EncodeError, match='address operand is unsupported'):
            obj._validate_tokens(0, 4)
    finally:
        obj.close()


def test_require_header_reduces_only_temporary_value_construction(monkeypatch):
    obj = owner([ADDRESS, REG, REG, SHIFT])
    opcode = sem.semantic_opcode_id('ldr')
    obj.headers.append4(opcode, sem.FORM_CANONICAL, 0, 4)
    constructed = []
    validated = []
    original_value = values.CompilerInt4
    original_validate = sem.validate_semantic_token
    def value(*parts):
        constructed.append(parts)
        return original_value(*parts)
    def validate(*parts):
        validated.append(parts)
        return original_validate(*parts)
    monkeypatch.setattr(values, 'CompilerInt4', value)
    monkeypatch.setattr(sem, 'validate_semantic_token', validate)
    try:
        header = obj.require_header(0)
        assert (header.first, header.second, header.third, header.fourth) == (opcode, sem.FORM_CANONICAL, 0, 4)
        assert validated == [ADDRESS, REG, REG, SHIFT]
        # Instrumentation-only path witness. Disabling the host path restores
        # ten token projections plus this one header, and must fail this test.
        assert constructed == [(opcode, sem.FORM_CANONICAL, 0, 4)]
    finally:
        obj.close()


@pytest.mark.parametrize('slot,message', (
    (1, 'address base is not an X/SP register'),
    (2, 'address index is invalid'),
    (3, 'address shift is invalid'),
))
def test_address_validator_mutation_is_seen_before_subgroup_read(slot, message, monkeypatch):
    obj = owner([ADDRESS, REG, REG, SHIFT])
    calls = []
    original = sem.validate_semantic_token
    def validate(*parts):
        calls.append(parts)
        original(*parts)
        if len(calls) == 1:
            obj.tokens.set(slot * 4, sem.TOKEN_IMMEDIATE)
    monkeypatch.setattr(sem, 'validate_semantic_token', validate)
    try:
        with pytest.raises(EncodeError, match=message):
            obj._validate_tokens(0, 4)
        assert calls == [ADDRESS]
    finally:
        obj.close()


def test_custom_first_addition_preserves_bound_owner_before_index_evaluation(monkeypatch):
    address = (sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0)
    obj = owner([address, REG, IMM])
    old_tokens = obj.tokens
    replacement = values.CompilerIntArena()
    replacement.append4(*address)
    replacement.append4(sem.TOKEN_REGISTER, 999, 0, 0)
    replacement.append4(*IMM)
    calls = []
    original = sem.validate_semantic_token
    class First(int):
        def __add__(self, other):
            if other == 1:
                obj.tokens = replacement
            return int(self) + other
    def validate(*parts):
        calls.append(parts)
        return original(*parts)
    monkeypatch.setattr(sem, 'validate_semantic_token', validate)
    try:
        # Evaluation of self.tokens.get4_unchecked(index + 1) binds the old
        # owner before First.__add__ replaces it. The old base passes the
        # address check. Only the later outer token validation sees register
        # 999. A host helper incorrectly entered here fails earlier with the
        # distinct "address base" error instead.
        with pytest.raises(EncodeError, match='operand is invalid or unsupported'):
            obj._validate_tokens(First(0), 3)
        assert calls == [address, (sem.TOKEN_REGISTER, 999, 0, 0)]
    finally:
        obj.close()
        if obj.tokens is not old_tokens:
            old_tokens.close()
        else:
            replacement.close()

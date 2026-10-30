"""Name memoization preserves grammar roles, custom strings and memory bounds."""
import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_parse as parser


def test_ssa_proof_does_not_admit_an_invalid_emitted_symbol():
    parser.retire_module_parse_caches()
    assert parser._ascii_plain_ir_name("valid-ssa")
    with pytest.raises(BackendUnavailable, match="simple C identifier"):
        parser.check_simple_symbol_name("valid-ssa")
    parser.check_simple_symbol_name("valid_symbol.1")
    assert parser._ascii_plain_ir_name("valid_symbol.1")
    with pytest.raises(BackendUnavailable):
        parser.check_simple_symbol_name("9invalid")
    assert not parser._ascii_plain_ir_name("9invalid")


class ChangingString(str):
    __hash__ = None

    def __init__(self, value):
        self.invalid = False

    def __getitem__(self, index):
        return "/" if self.invalid else super().__getitem__(index)


def test_custom_string_observations_are_not_cached():
    value = ChangingString("valid")
    assert parser._ascii_plain_ir_name(value)
    parser.check_simple_symbol_name(value)
    value.invalid = True
    assert not parser._ascii_plain_ir_name(value)
    with pytest.raises(BackendUnavailable):
        parser.check_simple_symbol_name(value)


def test_validation_storage_is_bounded_and_retired(monkeypatch):
    parser.retire_module_parse_caches()
    monkeypatch.setattr(parser, "_VALIDATED_NAME_LIMIT", 4)
    for index in range(40):
        name = "name_" + str(index)
        assert parser._ascii_plain_ir_name(name)
        parser.check_simple_symbol_name(name)
        assert len(parser._VALIDATED_NAME_FLAGS) <= 4
    parser.retire_module_parse_caches()
    assert not parser._VALIDATED_NAME_FLAGS


def test_new_module_drops_prior_validation_proofs():
    parser.check_simple_symbol_name("only_in_the_previous_module")
    parser.parse_self_backend_module('''target triple = "arm64-apple-darwin"
define i32 @main() {
entry:
  ret i32 0
}
''')
    assert "only_in_the_previous_module" not in parser._VALIDATED_NAME_FLAGS

"""Finite host semantic differential for exact packed symbol resolution.

The reference is the verbatim pre-change resolver from authenticated blob
437261a162096a322bd727e73f010e4c1334a8c0. No PCO/native compiler is invoked.
"""
import pytest
from pcc.backend import macho_link as link, macho_spec as spec, native_object as native
from pcc.backend.macho_link import LinkError, _symbol_name, _symbol_type


def _original_relocation_symbol_name(
    symbols,
    symbol_index: int,
    local_rename: dict[str, str],
    *,
    context: str,
) -> str:
    if not 0 <= symbol_index < len(symbols):
        raise LinkError(
            f"{context} names symbol index {symbol_index}, but the input has "
            f"{len(symbols)} symbols"
        )
    symbol = symbols[symbol_index]
    name = _symbol_name(symbol)
    if (
        (_symbol_type(symbol) & spec.N_TYPE) == spec.N_SECT
        and not (_symbol_type(symbol) & spec.N_EXT)
    ):
        return local_rename.get(name, name)
    return name


@pytest.mark.parametrize("section_index", (0, 3))
@pytest.mark.parametrize("flags", (0, 1, 2, 3))
def test_exact_packed_partition_matches_original(section_index, flags):
    # Private-external affects neither the N_TYPE mask nor the N_EXT bit.
    for name in ("symbol", ""):
        for renames in ({}, {name: "renamed"}):
            symbol = native.PackedNativeSymbol(name, section_index, 0, flags)
            expected = _original_relocation_symbol_name([symbol], 0, renames, context="packed")
            actual = link._relocation_symbol_name([symbol], 0, renames, context="packed")
            assert actual == expected
            assert actual == (renames.get(name, name) if section_index and not flags & 1 else name)


@pytest.mark.parametrize("index", (-1, 1, 1000000, "invalid"))
def test_invalid_index_preserves_error_and_access_order(index):
    class Symbols:
        def __init__(self, events):
            self.events = events

        def __len__(self):
            self.events.append("len")
            return 1

        def __getitem__(self, key):
            self.events.append(("getitem", key))
            raise AssertionError("invalid index must not reach symbol fields")

    outcomes = []
    for resolver in (_original_relocation_symbol_name, link._relocation_symbol_name):
        events = []
        try:
            resolver(Symbols(events), index, {}, context="SUBTRACTOR minuend relocation")
        except Exception as error:
            outcomes.append((type(error), error.args, events))
        else:
            raise AssertionError("malformed index accepted")
    assert outcomes[0] == outcomes[1]


def _opaque_symbol(kind, events):
    class Flag(int):
        def __and__(self, other):
            events.append(("flags.and", other))
            return int(self) & other

    class Index(int):
        def __eq__(self, other):
            events.append(("index.eq", other))
            return int(self) == other

    class Name(str):
        def __hash__(self):
            events.append("name.hash")
            return super().__hash__()

    class PackedSubclass(native.PackedNativeSymbol):
        def __getattribute__(self, name):
            if name in ("name", "section_index", "flags", "external", "private_external"):
                events.append(("getattr", name))
            return super().__getattribute__(name)

    class Mapping(dict):
        def __getitem__(self, key):
            events.append(("mapping.getitem", key))
            return super().__getitem__(key)

    if kind == "native":
        return native.NativeSymbol("symbol", 1, 0, False)
    if kind == "subclass":
        return PackedSubclass("symbol", 1, 0, 0)
    if kind == "flag-subclass":
        return native.PackedNativeSymbol("symbol", 1, 0, Flag(0))
    if kind == "index-subclass":
        return native.PackedNativeSymbol("symbol", Index(1), 0, 0)
    if kind == "name-subclass":
        return native.PackedNativeSymbol(Name("symbol"), 1, 0, 0)
    if kind == "invalid-flags":
        return native.PackedNativeSymbol("symbol", 1, 0, "bad flags")
    if kind == "invalid-name":
        return native.PackedNativeSymbol([], 1, 0, 0)
    # Raw Mach-O n_type has kinds the packed descriptor cannot represent.
    kinds = {"abs": spec.N_ABS, "undef": spec.N_UNDF, "indirect": spec.N_INDR,
             "prebound": spec.N_PBUD, "section": spec.N_SECT}
    return Mapping(name="symbol", n_type=kinds[kind])


@pytest.mark.parametrize("kind", (
    "native", "subclass", "flag-subclass", "index-subclass", "name-subclass",
    "invalid-flags", "invalid-name", "abs", "undef", "indirect", "prebound", "section",
))
def test_opaque_fields_and_all_n_type_kinds_keep_fallback_order(kind):
    outcomes = []
    for resolver in (_original_relocation_symbol_name, link._relocation_symbol_name):
        events = []
        symbol = _opaque_symbol(kind, events)
        events.clear()
        try:
            value = resolver((symbol,), 0, {"symbol": "renamed"}, context="opaque")
        except Exception as error:
            outcome = ("error", type(error), error.args)
        else:
            outcome = ("value", str(value))
        outcomes.append((outcome, events))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("flags", (0, 1))
def test_rename_callback_and_error_happen_only_for_local_symbols(flags):
    class Rename:
        def __init__(self, events):
            self.events = events

        def get(self, name, default):
            self.events.append(("rename.get", name, default))
            raise LookupError("rename refusal")

    outcomes = []
    for resolver in (_original_relocation_symbol_name, link._relocation_symbol_name):
        events = []
        symbol = native.PackedNativeSymbol("symbol", 1, 0, flags)
        try:
            value = resolver([symbol], 0, Rename(events), context="packed")
        except Exception as error:
            outcome = ("error", type(error), error.args)
        else:
            outcome = ("value", value)
        outcomes.append((outcome, events))
    assert outcomes[0] == outcomes[1]


def test_exact_packed_branch_avoids_polymorphic_name_and_type_helpers(monkeypatch):
    def unexpected(*args):
        raise AssertionError("exact packed symbol used generic projection")

    monkeypatch.setattr(link, "_symbol_name", unexpected)
    monkeypatch.setattr(link, "_symbol_type", unexpected)
    for section_index in (0, 1):
        for flags in (0, 1, 2, 3, 4, 5, 6, 7):
            symbol = native.PackedNativeSymbol("symbol", section_index, 0, flags)
            expected = "renamed" if section_index and not flags & 1 else "symbol"
            assert link._relocation_symbol_name([symbol], 0, {"symbol": "renamed"}, context="packed") == expected


def test_symbol_sequence_getitem_order_is_unchanged():
    class Symbols:
        def __init__(self, events):
            self.events = events

        def __len__(self):
            self.events.append("len")
            return 1

        def __getitem__(self, index):
            self.events.append(("getitem", index))
            return native.PackedNativeSymbol("symbol", 1, 0, 0)

    outcomes = []
    for resolver in (_original_relocation_symbol_name, link._relocation_symbol_name):
        events = []
        value = resolver(Symbols(events), 0, {"symbol": "renamed"}, context="packed")
        outcomes.append((value, events))
    assert outcomes[0] == outcomes[1] == ("renamed", ["len", ("getitem", 0)])

"""Allocation-bounded wire sizing for ordinary immutable scalars and IR records.

The wire shape is owned by self_backend_indexed_codec. This opt-in count never
builds its wire lists, header string, or raw arenas. Reject as soon as a proven
prefix cost exceeds the remaining bytes. Unknown shapes leave the original
conservative rejection intact; no subclass conversions or iteration run here.
"""

from __future__ import annotations

from .self_backend_ir import (
    ArgInfo,
    GlobalDef,
    ParsedFunction,
    ParsedModule,
    TypeDesc,
    TypeParseContext,
)
from .self_backend_kernel import IndexedFunctionKernel
from .self_backend_value_arena import CompilerIntArena


def _object_keys(*keys: str) -> int:
    # Nonempty object: braces cancel the last absent ', '; each ASCII key
    # needs quotes, ': ', and ', '. All serializer-owned keys here are plain.
    return sum(len(key) + 6 for key in keys)


def _array_syntax(n: int) -> int:
    # '[' + (n-1) times ', ' + ']', or '[]'.
    return 2 * n if n else 2


def count_plain_wire(
    module, limit, arena_fields, magic, schema_v1, schema_odr,
    charge_json_string,
):
    """Return True for a proven fit, False for unknowns; abort excess with OverflowError."""
    if type(module) is not ParsedModule or limit <= 0:
        return False
    context = module.type_context
    if context is not None and (
        type(context) is not TypeParseContext or context.debug_info is not None
    ):
        return False

    spent = 0
    total_scalars = 0
    schema = schema_v1

    def charge(count):
        nonlocal spent
        spent += count
        if spent > limit:
            raise OverflowError("plain wire bytes exceed budget")

    def text(v):
        if type(v) is not str:
            return False
        charge_json_string(v, charge)
        return True

    def integer(v):
        # Ordinary metadata fits machine words. Keep the old arbitrary-int
        # bound, conversion policy and failures for outliers.
        if type(v) is not int or v.bit_length() > 64:
            return False
        charge(len(str(v)))  # at most a sign and 20 decimal digits
        return True

    def boolean(v):
        if type(v) is not bool:
            return False
        charge(4 if v else 5)
        return True

    def array(values, visit):
        if type(values) is not tuple and type(values) is not list:
            return False
        charge(_array_syntax(len(values)))
        for value in values:
            if not visit(value):
                return False
        return True

    def raw_type(v):
        if v is None:
            charge(4)
            return True
        if type(v) is not TypeDesc:
            return False
        charge(_array_syntax(7))
        return (text(v.kind) and integer(v.width) and raw_type(v.pointee)
                and integer(v.count) and raw_type(v.elem) and text(v.name)
                and array(v.fields, raw_type))

    def cold(v):
        kind = type(v)
        if v is None:
            charge(4)
        elif kind is bool:
            return boolean(v)
        elif kind is int:
            return integer(v)
        elif kind is str:
            return text(v)
        elif kind is float:
            # Python JSON spells NaN (same length); +/-inf become +/-Infinity.
            scalar = repr(v)
            charge(8 if scalar == "inf" else 9 if scalar == "-inf" else len(scalar))
        elif kind is TypeDesc:
            charge(7)  # {"t": value}
            return raw_type(v)
        elif kind is tuple or kind is list:
            charge(7)  # {"q": [...]} or {"l": [...]}
            return array(v, cold)
        elif kind is dict:
            charge(7 + _array_syntax(len(v)) + 4 * len(v))
            for key, value in v.items():  # {"d": [[key, value], ...]}
                if not cold(key) or not cold(value):
                    return False
        else:
            return False
        return True

    def global_row(g):
        if type(g) is not GlobalDef:
            return False
        charge(_array_syntax(9))
        return (text(g.name) and raw_type(g.type) and text(g.initializer)
                and boolean(g.is_constant) and boolean(g.is_internal)
                and text(g.tls_model) and integer(g.alignment)
                and text(g.ir_prefix) and array(g.trailing_attributes, text))

    def arg_row(arg):
        if type(arg) is not ArgInfo:
            return False
        charge(_array_syntax(2))
        return text(arg.name) and raw_type(arg.type)

    function_keys = _object_keys(
        "name", "ret_type", "args", "is_global", "is_vararg", "block_names",
        "value_names", "first_duplicate_definition_value_id", "instruction_use_total",
        "instruction_arithmetic_flags", "cold_instruction_data", "call_texts",
        "types", "arenas",
    )

    def function_row(f):
        nonlocal total_scalars, schema
        if (type(f) is not ParsedFunction
                or type(f.indexed_kernel) is not IndexedFunctionKernel):
            return False
        k = f.indexed_kernel
        charge(function_keys)
        linkage = f.definition_linkage
        if type(linkage) is not str:
            return False
        if linkage == "external_odr":
            schema = schema_odr
            charge(_object_keys("definition_linkage"))
            text(linkage)
        elif linkage not in ("", "internal", "external"):
            return False

        charge(_array_syntax(len(arena_fields)))
        for name, field, _seed_field in arena_fields:
            arena = getattr(k, field)
            if type(arena) is not CompilerIntArena or type(arena._length) is not int:
                return False
            n = len(arena)
            total_scalars += n
            charge(n * 8 + _array_syntax(2))
            text(name)
            integer(n)

        flags = k.instruction_arithmetic_flag_values
        if type(flags) is not dict:
            return False
        charge(_array_syntax(len(flags)) + 4 * len(flags))
        for key, values in flags.items():
            if not integer(key) or not array(values, text):
                return False

        return (text(f.name) and raw_type(f.ret_type) and array(f.args, arg_row)
                and boolean(f.is_global) and boolean(f.is_vararg)
                and array(k.block_names, text) and array(k.value_names, text)
                and integer(k.first_duplicate_definition_value_id)
                and integer(k.instruction_use_total) and array(k.call_texts, text)
                and array(k.types, raw_type) and array(k.cold_instruction_data, cold))

    charge(_object_keys(
        "schema", "scalar_width", "byte_order", "triple", "named_type_bodies",
        "globals", "functions", "total_scalars",
    ))
    charge(1 + len('"little"'))  # scalar width 8 and byte order
    if not text(module.triple):
        return False
    if context is None:
        charge(2)
    else:
        names = context.named_type_bodies
        if type(names) is not dict:
            return False
        # Actual raw object, not a {"d": [...]} cold record.
        charge(4 * len(names) if names else 2)
        for key, value in names.items():
            if not text(key) or not text(value):
                return False
    if not array(module.globals_, global_row) or not array(module.functions, function_row):
        return False
    text(schema)
    if not integer(total_scalars):
        return False
    header = spent - total_scalars * 8
    charge(len(magic) + 1 + len(str(header)))
    return True

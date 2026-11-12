"""GNU __attribute__ semantics for the C frontend: honored, inert or an explicit gap.

The parser keeps every attribute (GnuAttribute) on its declaration, typedef or
record. This owner decides, before code generation, what each one means:

* inert: attributes that only steer diagnostics, optimization or symbol
  visibility in a way this compiler does not depend on (noinline, unused,
  format, noreturn, visibility, ...). Ignoring them preserves C semantics.
* honored: ``aligned`` on objects, members and records, ``packed`` on records
  and members, and integer ``mode`` (rewritten by the parser).
* every other attribute, or an honored one in a position this owner does not
  model, is a CodegenError gap, never a silently erased semantic.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.codegen.c_declaration_state import CodegenError

INERT = frozenset({
    "noinline", "noipa", "noclone", "always_inline", "flatten", "hot", "cold", "optimize", "target",
    "unused", "used", "maybe_unused", "deprecated", "unavailable", "warn_unused_result", "nodiscard",
    "noreturn", "const", "pure", "malloc", "alloc_size", "alloc_align", "assume_aligned",
    "nonnull", "returns_nonnull", "format", "format_arg", "sentinel", "nothrow", "leaf", "artificial",
    "access", "no_instrument_function", "no_sanitize", "no_sanitize_address", "no_sanitize_thread",
    "no_sanitize_undefined", "no_stack_protector", "stack_protect", "nonstring", "may_alias",
    "designated_init", "fallthrough", "externally_visible", "no_icf", "no_reorder", "retain",
    "visibility", "tls_model", "error", "warning", "warn_if_not_aligned", "no_split_stack", "noplt",
    "regparm", "cdecl", "stdcall", "fastcall", "no_profile_instrument_function", "patchable_function_entry",
    "nocommon", "common", "uninitialized", "strict_flex_array", "counted_by", "copy",
})
HONORED = frozenset({"aligned", "packed"})


def gnu_name(attribute):
    return attribute.base_name


def attributes_named(attributes, name):
    return [attribute for attribute in attributes or () if gnu_name(attribute) == name]


def gnu_alignment(attributes, evaluate):
    """The strictest ``aligned`` of a list; bare ``aligned`` is the target maximum (16)."""
    values = []
    for attribute in attributes_named(attributes, "aligned"):
        if not attribute.args:
            values.append(16)
            continue
        if len(attribute.args) != 1:
            raise CodegenError("GNU aligned takes at most one argument")
        value = evaluate(attribute.args[0])
        if value <= 0 or value & (value - 1):
            raise CodegenError(f"GNU aligned({value}) is not a positive power of two")
        values.append(value)
    return max(values) if values else None


def _gap(attribute, where):
    location = f"{attribute.coord}: " if attribute.coord else ""
    raise CodegenError(f"{location}GNU attribute '{attribute.name}' on {where} is not implemented")


def _check(attributes, where, honored):
    for attribute in attributes or ():
        if attribute.name == "__asm_label__":
            location = f"{attribute.coord}: " if attribute.coord else ""
            raise CodegenError(f"{location}asm labels that rename a declaration's symbol are not implemented")
        name = gnu_name(attribute)
        if name in INERT or name in honored:
            continue
        _gap(attribute, where)


def check_translation_unit(ast):
    """Reject every attribute whose meaning is not modeled; return nothing."""
    pending = [ast]
    while pending:
        node = pending.pop()
        if node is None:
            continue
        if isinstance(node, (c_ast.Struct, c_ast.Union)):
            _check(node.attributes, "a struct or union", HONORED)
            if attributes_named(node.attributes, "packed") and node.decls and any(
                    decl.bitsize is not None for decl in node.decls):
                _gap(attributes_named(node.attributes, "packed")[0], "a record with bit-fields")
        elif isinstance(node, c_ast.Enum):
            _check(node.attributes, "an enum", frozenset())
        elif isinstance(node, c_ast.Typedef):
            _check(node.attributes, "a typedef", frozenset())
        elif isinstance(node, c_ast.Decl):
            _check(node.attributes, "a declaration", HONORED)
        elif isinstance(node, c_ast.PtrDecl):
            _check(getattr(node, "attributes", ()), "a pointer declarator", frozenset())
        pending.extend(child for _name, child in node.children())

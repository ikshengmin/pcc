"""GNU __attribute__ semantics for the C frontend: honored, inert or an explicit gap.

The parser keeps every attribute (GnuAttribute) on its declaration, typedef or
record. This owner decides, before code generation, what each one means:

* inert: attributes that only steer diagnostics, optimization or symbol
  visibility in a way this compiler does not depend on (noinline, unused,
  format, noreturn, visibility, ...). Ignoring them preserves C semantics.
* honored: ``aligned`` on objects, members, records and typedefs, ``packed``
  on records, members and bit-fields, integer ``mode`` (rewritten by the
  parser), and ``weak``, ``constructor`` and ``destructor`` on file-scope
  function definitions.
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


class TypedefAlignment(c_ast.GnuAttribute):
    """An ``aligned`` attribute a declaration inherits from its typedef name."""
    __slots__ = ('typedef',)

    def __init__(self, attribute, typedef):
        super().__init__(attribute.name, attribute.args, attribute.coord)
        self.typedef = typedef


def _typedef_name(type_node):
    """The single typedef name a TypeDecl spells, or None."""
    if isinstance(type_node, c_ast.TypeDecl) and isinstance(type_node.type, c_ast.IdentifierType):
        names = type_node.type.names
        if len(names) == 1:
            return names[0]
    return None


def propagate_typedef_alignment(ast):
    """Give each object or member declared with an aligned typedef name that alignment.

    GCC "Common Type Attributes": ``aligned`` on a typedef applies to every
    object of that typedef. Objects and members spelled with the name inherit
    it (codegen honors it and rejects a lowering); every other use of the
    name (casts, sizeof/_Alignof, pointers, arrays, parameters) is a gap.
    """
    aligned = {}
    contexts = {c_ast.Typename: "type name", c_ast.PtrDecl: "pointer declarator",
                c_ast.ArrayDecl: "array declarator", c_ast.FuncDecl: "function declarator"}
    pending = [(ast, "declaration")]
    while pending:
        node, context = pending.pop()
        if node is None:
            continue
        if isinstance(node, c_ast.Typedef):
            name = _typedef_name(node.type)
            inherited = list(aligned.get(name, ())) if name else []
            own = list(attributes_named(node.attributes, "aligned"))
            if own or inherited:
                aligned[node.name] = inherited + own
            if not inherited:
                pending.append((node.type, "typedef"))
            continue
        if isinstance(node, c_ast.Decl) and context != "parameter":
            name = _typedef_name(node.type)
            if name in aligned and node.bitsize is None:
                if not any(isinstance(attribute, TypedefAlignment) for attribute in node.attributes or ()):
                    node.attributes = tuple(node.attributes or ()) + tuple(
                        TypedefAlignment(attribute, name) for attribute in aligned[name])
                pending.append((node.init, "expression"))
                continue
        if isinstance(node, c_ast.IdentifierType):
            for name in node.names:
                if name in aligned:
                    _gap(aligned[name][0], f"a typedef ('{name}') used in a {context}")
            continue
        if isinstance(node, c_ast.ParamList):
            pending.extend((param, "parameter") for param in reversed(node.params or ()))
            continue
        child_context = contexts.get(type(node), context)
        pending.extend((child, child_context) for _name, child in reversed(node.children()))
    return ast


DEFINITION_ATTRIBUTES = ("weak", "constructor", "destructor")


def function_definition_attributes(ast):
    """Attach a file-scope function's weak/constructor/destructor to its definition.

    GCC "Common Function Attributes": a weak definition is emitted as a weak
    symbol that a strong definition elsewhere overrides; a constructor runs
    before main and a destructor after it. Each must name a function this
    unit defines (an undefined weak reference and a constructor defined
    elsewhere are gaps); a weak static function is an error. Returns the ids
    of the declarations allowed these attributes.
    """
    found, definitions, declarations = {}, {}, {}
    for ext in getattr(ast, "ext", None) or ():
        decl = ext.decl if isinstance(ext, c_ast.FuncDef) else ext if isinstance(ext, c_ast.Decl) else None
        if decl is None or not isinstance(decl.type, c_ast.FuncDecl):
            continue
        declarations.setdefault(decl.name, []).append(decl)
        for name in DEFINITION_ATTRIBUTES:
            for attribute in attributes_named(decl.attributes, name):
                found.setdefault(decl.name, {}).setdefault(name, attribute)
        if isinstance(ext, c_ast.FuncDef):
            definitions[decl.name] = ext
    allowed = set()
    for function, attributes in found.items():
        definition = definitions.get(function)
        for name, attribute in attributes.items():
            if definition is None:
                where = (f"the undefined function '{function}' (a weak reference)" if name == "weak"
                         else f"'{function}', which this translation unit does not define")
                _gap(attribute, where)
            if name == "weak" and any("static" in decl.storage for decl in declarations[function]):
                raise CodegenError(f"weak declaration of '{function}' must be public")
            if not attributes_named(definition.decl.attributes, name):
                definition.decl.attributes = tuple(definition.decl.attributes or ()) + (attribute,)
        allowed.update(id(decl) for decl in declarations[function])
    return allowed


def check_translation_unit(ast):
    """Reject every attribute whose meaning is not modeled; return nothing."""
    propagate_typedef_alignment(ast)
    definition_declarations = function_definition_attributes(ast)
    pending = [ast]
    while pending:
        node = pending.pop()
        if node is None:
            continue
        if isinstance(node, (c_ast.Struct, c_ast.Union)):
            _check(node.attributes, "a struct or union", HONORED)
        elif isinstance(node, c_ast.Enum):
            _check(node.attributes, "an enum", frozenset())
        elif isinstance(node, c_ast.Typedef):
            _check(node.attributes, "a typedef", frozenset({"aligned"}))
        elif isinstance(node, c_ast.Decl):
            _check(node.attributes, "a declaration",
                   HONORED | set(DEFINITION_ATTRIBUTES) if id(node) in definition_declarations else HONORED)
        elif isinstance(node, c_ast.PtrDecl):
            _check(getattr(node, "attributes", ()), "a pointer declarator", frozenset())
        pending.extend(child for _name, child in node.children())

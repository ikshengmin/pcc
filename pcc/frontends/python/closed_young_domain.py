"""Fail-closed admission for the first independent GC3 young collection.

This is deliberately not a general closed-world import proof.  Only the
single-source, import-free executable route can currently issue admission.
Workers, reusable IR, custom link inputs and imported source closures remain
denied until their provenance is carried through the whole build/cache route.
Denial changes no Python semantics: the existing promotion/major paths remain.
"""

from __future__ import annotations

from typing import Optional

from pcc.backend.self_backend_target_match import (
    is_aarch64_linux_triple,
    is_x86_64_linux_triple,
)


# Explicit executable children, not semantic Type graphs. Unknown syntax is
# denied rather than accidentally treated as a leaf by a generic AST walker.
# Defaults, decorators, lambdas and every nested body belong to the proof.
_SYNTAX_CHILD_FIELDS = {
    "Module": ("body",),
    "FuncDef": ("args", "body", "decorators"),
    "ClassDef": ("bases", "keywords", "body", "decorators"),
    "Arg": ("default",),
    "ExceptHandler": ("exc_type", "body"),
    "Name": (),
    "IntLit": (),
    "FloatLit": (),
    "ComplexLit": (),
    "BoolLit": (),
    "NoneLit": (),
    "StrLit": (),
    "BytesLit": (),
    "BinOp": ("lhs", "rhs"),
    "UnaryOp": ("operand",),
    "Compare": ("lhs", "rhs"),
    "BoolExpr": ("left", "right"),
    "Call": ("func", "args", "kwargs"),
    "Attr": ("obj",),
    "Subscript": ("obj", "idx"),
    "Slice": ("lo", "hi", "step"),
    "ListExpr": ("elems",),
    "DictExpr": ("pairs",),
    "TupleExpr": ("elems",),
    "IfExpr": ("cond", "then_e", "else_e"),
    "Lambda": ("params", "body"),
    "Assign": ("targets", "value"),
    "AugAssign": ("target", "value"),
    "ExprStmt": ("expr",),
    "If": ("cond", "body", "else_body"),
    "While": ("cond", "body", "else_body"),
    "For": ("target", "iter", "body", "else_body"),
    "Return": ("value",),
    "Pass": (),
    "Break": (),
    "Continue": (),
    "Raise": ("exc", "cause"),
    "Try": ("body", "handlers", "else_body", "finally_body"),
    "With": ("items", "body"),
    "Global": (),
    "Nonlocal": (),
    "Delete": ("targets",),
}

_OPEN_DOMAIN_NAMES = frozenset((
    "breakpoint", "compile", "eval", "exec", "getattr", "globals",
    "help", "locals", "vars", "setattr", "delattr", "c_abi_export", "c_abi_typed_export",
    "c_abi_variadic_export",
))
_PLAIN_DUNDER_NAMES = frozenset((
    "__name__", "__file__", "__doc__", "__package__", "__debug__",
))


def closed_young_module_denial(module) -> str:
    """Return an empty string only for the admitted source capability set."""
    name = module.name or ""
    if name == "pcc" or name.startswith("pcc.") or name == "bootstrap" or name.startswith("bootstrap."):
        return "raw compiler/runtime module namespace"
    work = [module]
    index = 0
    while index < len(work):
        node = work[index]
        index += 1
        if node is None or isinstance(node, (str, int, float, bytes)):
            continue
        if isinstance(node, tuple):
            work.extend(node)
            continue
        kind = type(node).__name__
        if kind.startswith("_"):
            kind = kind[1:]
        if kind in ("Import", "ImportFrom"):
            return "imports require a complete dependency-closure proof"
        fields = _SYNTAX_CHILD_FIELDS.get(kind)
        if fields is None:
            return "unverified syntax: " + kind
        if kind == "Name":
            ident = node.ident
            if ident in ("__pcc_freestanding__", "__pcc_runtime_port__"):
                return "raw runtime marker"
            if ident in _OPEN_DOMAIN_NAMES or (
                ident.startswith("__") and ident not in _PLAIN_DUNDER_NAMES
            ):
                return "dynamic or foreign capability: " + ident
        elif kind == "Attr":
            if node.name.startswith("__") or node.name in _OPEN_DOMAIN_NAMES:
                return "reflective or foreign attribute: " + node.name
        elif kind == "FuncDef":
            if node.manual_pointer_abi:
                return "manual pointer ABI"
            if node.is_async:
                return "unverified asynchronous callable"
            if node.decorators:
                return "unverified function decorator"
        elif kind == "ClassDef" and node.decorators:
            return "unverified class decorator"
        elif kind in ("For", "With") and node.is_async:
            return "unverified asynchronous protocol"
        for field in fields:
            value = getattr(node, field)
            if isinstance(value, tuple):
                work.extend(value)
            else:
                work.append(value)
    return ""


def closed_young_request_denial(
    source_count: int,
    emit_llvm_only: bool,
    python_library: bool,
    backend: str,
    gpu_backend: str,
    runtime_archive: Optional[str],
    object_inputs,
    link_args,
    environment,
    target_triple: str,
) -> str:
    """Prove the terminal link request, independently of its source AST."""
    if source_count != 1:
        return "multiple sources require a dependency-closure proof"
    if emit_llvm_only or python_library:
        return "reusable IR/library output has an unknown final link closure"
    if backend != "self":
        return "unverified native backend"
    # These are exactly the targets accepted by owned_elf_link.link_inputs,
    # which emits and verifies a static executable. An owned Mach-O/PE linker
    # alone does not prove the startup dynamic-library closure. Runtime loader
    # environment checks cannot replace this terminal-link boundary.
    if not (is_x86_64_linux_triple(target_triple)
            or is_aarch64_linux_triple(target_triple)):
        return "unverified startup closure: requires owned static Linux ELF"
    if gpu_backend != "none":
        return "unverified device/host bridge"
    if object_inputs or link_args:
        return "external object or linker inputs"
    if runtime_archive is not None:
        return "explicit runtime archive has no closed-domain proof"
    for name in ("PCC_RUNTIME_ARCHIVE", "PCC_RUNTIME_DIR", "PCC_DEFER_SELF_LINK_PLAN"):
        if str(environment.get(name, "") or "").strip():
            return "unverified build override: " + name
    if str(environment.get("PCC_RUNTIME_BUILD", "owned") or "owned").strip() != "owned":
        return "unverified runtime construction"
    if str(environment.get("PCC_SELF_LINK", "") or "").strip() not in ("", "pcc"):
        return "unverified native linker"
    return ""

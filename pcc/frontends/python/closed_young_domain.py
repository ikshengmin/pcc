"""Fail-closed admission for the first independent GC3 young collection.

This is deliberately not a general closed-world import proof. Imports require
an explicit source closure whose provenance survives the build/cache route.
An import-free caller retains the original single-source admission behavior.
Denial changes no Python semantics: the existing promotion/major paths remain.
"""

from __future__ import annotations

import os
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


def _closed_young_raw_namespace(name: str) -> bool:
    return (name == "pcc" or name.startswith("pcc.")
            or name == "bootstrap" or name.startswith("bootstrap."))


def _closed_young_module_name(name) -> bool:
    return (isinstance(name, str) and bool(name)
            and all(part.isidentifier() for part in name.split(".")))


def _closed_young_import_provider(node, module_name, module_path):
    """Resolve a from-import without the normal compiler's permissive fallback."""
    provider = node.module
    level = node.level
    if not isinstance(provider, str) or type(level) is not int or level < 0:
        return "", "malformed import provider"
    if level:
        if not module_path or not _closed_young_module_name(module_name):
            return "", "relative import requires an actual source identity"
        parts = module_name.split(".")
        package = parts if os.path.basename(module_path) == "__init__.py" else parts[:-1]
        # level == len(package) is the outermost legal package. Removing all
        # package components must not turn an invalid relative import absolute.
        if level > len(package):
            return "", "relative import escapes its source package"
        base = package[:len(package) - level + 1]
        provider = ".".join(base + ([provider] if provider else []))
    if not _closed_young_module_name(provider):
        return "", "unresolved import provider"
    return provider, ""


def _closed_young_import_denial(node, module_name, module_path, closure_modules):
    if not closure_modules:
        return "imports require a complete dependency-closure proof"
    kind = type(node).__name__.lstrip("_")
    providers = []
    if kind == "ImportFrom":
        provider, reason = _closed_young_import_provider(node, module_name, module_path)
        if reason:
            return reason
        providers.append(provider)
    if not isinstance(node.names, (tuple, list)) or not node.names:
        return "malformed import bindings"
    for binding in node.names:
        if not isinstance(binding, (tuple, list)) or len(binding) != 2:
            return "malformed import binding"
        imported, alias = binding
        if imported == "*":
            return "star import has no exact provider proof"
        if not _closed_young_module_name(imported):
            return "unresolved import binding"
        if alias is not None and (not isinstance(alias, str) or not alias.isidentifier()):
            return "malformed import alias"
        if kind == "Import":
            providers.append(imported)
        elif "." in imported:
            return "malformed from-import binding"
        elif imported.startswith("__") or imported in _OPEN_DOMAIN_NAMES:
            return "dynamic or foreign import binding: " + imported
    for provider in providers:
        if _closed_young_raw_namespace(provider):
            return "raw compiler/runtime import namespace: " + provider
        # Importing pkg.child also executes pkg. Every prefix is a provider.
        parts = provider.split(".")
        for count in range(1, len(parts) + 1):
            prefix = ".".join(parts[:count])
            if prefix not in closure_modules:
                return "import provider outside source closure: " + prefix
    return ""


def closed_young_module_denial(module, *, closure_modules=(), module_path="") -> str:
    """Return an empty string only for the admitted source capability set."""
    name = module.name or ""
    if _closed_young_raw_namespace(name):
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
            reason = _closed_young_import_denial(node, name, module_path, closure_modules)
            if reason:
                return reason
            continue
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
    return closed_young_terminal_request_denial(
        emit_llvm_only, python_library, backend, gpu_backend, runtime_archive,
        object_inputs, link_args, environment, target_triple,
    )


def closed_young_terminal_request_denial(
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
    """Check terminal link ownership; this does not prove any source closure."""
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

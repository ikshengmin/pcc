"""Narrow source receipts for managed-Python GC3 young admission.

This proves neither a general closed world nor an FFI/thread/runtime closure.
The terminal driver must separately establish those boundaries. Receipts are
reconstructed from complete sources, never trusted as assertion flags; callers
must retain the exact checked typed object through code generation.
"""

from __future__ import annotations

import hashlib
import json
import os

from pcc.frontends.python.closed_young_domain import (
    _OPEN_DOMAIN_NAMES,
    _PLAIN_DUNDER_NAMES,
    _closed_young_import_denial,
    _closed_young_import_provider,
    _closed_young_module_name,
    _closed_young_raw_namespace,
    closed_young_module_denial,
)
from pcc.frontends.python.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES


_SCHEMA = "pcc.closed_young_closure"
_VERSION = 2
_PROOF_KEYS = {"schema", "version", "entry_module", "modules", "digest"}
_MODULE_KEYS = {"name", "path", "sha256", "syntax_sha256", "untyped_sha256"}
_TRANSPORT_SCHEMA = "pcc.closed_young_transport"
_TRANSPORT_VERSION = 1
_TRANSPORT_KEYS = {
    "schema", "version", "exports", "ast_dir", "assigned_indices", "asts", "digest",
}

# Examine the native parser's original tree before lifting can erase an
# annotation, lower a decorator, or inject a helper. Unknown shapes are denied.
_RAW_CHILD_FIELDS = {
    "_Module": ("body",), "_Pass": (), "_Break": (), "_Continue": (),
    "_Return": ("value",), "_Expr": ("expr",),
    "_Num": (), "_ComplexNum": (), "_Str": (), "_Bytes": (),
    "_Name": (), "_Bool": (), "_None": (),
    "_BinOp": ("lhs", "rhs"), "_UnaryOp": ("operand",),
    "_BoolOp": ("values",), "_Compare": ("lhs", "rhs"),
    "_Call": ("func", "args"), "_Attr": ("obj",),
    "_Subscript": ("obj", "idx"), "_Slice": ("lo", "hi", "step"),
    "_List": ("elems",), "_Tuple": ("elems",),
    "_Dict": ("keys", "values"), "_Set": ("elems",),
    "_Ternary": ("cond", "then_expr", "else_expr"),
    "_Lambda": ("params", "body"),
    "_Assign": ("target", "value", "annotation"),
    "_AugAssign": ("target", "value"),
    "_If": ("cond", "body", "else_body"),
    "_While": ("cond", "body", "else_body"),
    "_For": ("target", "iter", "body", "else_body"),
    "_FuncDef": ("params", "body", "decorators", "returns", "type_params"),
    "_ClassDef": ("bases", "body", "decorators", "type_params"),
    "_Raise": ("exc", "cause"),
    "_Try": ("body", "handlers", "else_body", "finally_body"),
    "_With": ("items", "body"), "_Del": ("targets",),
    "_Global": (), "_Nonlocal": (), "_Assert": ("test", "msg"),
}


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _canonical_path(path):
    path = os.fspath(path)
    if not isinstance(path, str) or not path:
        raise ValueError("missing source path")
    return os.path.realpath(os.path.abspath(path))


def _emitted_namespace_denial(names):
    from pcc.frontends.python.codegen.module_name_lowering import module_symbol_suffix

    owners = {}
    for name in names:
        suffix = module_symbol_suffix(name)
        if not isinstance(suffix, str) or not suffix:
            return "unverified emitted module namespace: " + name
        if suffix in owners:
            return "duplicate emitted module namespace: " + owners[suffix] + " and " + name
        owners[suffix] = name
    return ""


def _native_extension_provider_denial(resolver, name):
    # This is exactly the production filesystem selector, not an import or a
    # loader. A source receipt cannot certify the selected extension's effects.
    path = resolver._resolve_pcc_native_extension_path(name)
    if path is not None:
        return "selected native extension provider requires an owned-effect proof: " + name
    return ""


def _intercepted_provider_denial(names):
    # Lazy imports keep this receipt module independent of codegen's import
    # initialization. These are production rejection policies, not permission
    # to admit a module by its spelling. Even a supplied harmless os.py cannot
    # certify the owned/native provider selected ahead of its source exports.
    from pcc.frontends.python import pipeline_import_policy
    from pcc.frontends.python.codegen import layer1_constants
    from pcc.frontends.python.codegen.import_lowering import ImportLoweringMixin

    policies = (
        ImportLoweringMixin._NATIVE_BUILTIN_IMPORT_MODULES,
        ImportLoweringMixin._COMPILE_TIME_ONLY_MODULES,
        ImportLoweringMixin._COMPILE_TIME_ONLY_IMPORT_FROMS,
        ImportLoweringMixin._TEST_FACADE_IMPORT_MODULES,
        ImportLoweringMixin._ANNOTATION_ONLY_IMPORT_MODULES,
        pipeline_import_policy.NATIVE_BUILTIN_IMPORTS,
        pipeline_import_policy.COMPILE_TIME_ONLY_IMPORT_MODULES,
        pipeline_import_policy.COMPILE_TIME_ONLY_IMPORT_FROMS,
        pipeline_import_policy.TEST_FACADE_IMPORT_MODULES,
        pipeline_import_policy.ANNOTATION_ONLY_IMPORT_MODULES,
        layer1_constants.COMPILE_TIME_ONLY_MODULES,
        layer1_constants.COMPILE_TIME_ONLY_IMPORT_FROMS,
        layer1_constants.TEST_FACADE_IMPORT_MODULES,
        layer1_constants.ANNOTATION_ONLY_IMPORT_MODULES,
    )
    intercepted_roots = set()
    for policy in policies:
        intercepted_roots.update(name.split(".")[0] for name in policy)
    for name in names:
        if name.split(".")[0] in intercepted_roots:
            return "compiler-intercepted import provider requires an owned-effect proof: " + name
    return ""


def _source_denial(module, name, path, names):
    work = [module]
    imports = []
    while work:
        node = work.pop()
        if node is None or isinstance(node, (str, int, float, bytes)):
            continue
        if isinstance(node, (tuple, list)):
            work.extend(node)
            continue
        kind = type(node).__name__
        if kind in ("_Import", "_ImportFrom"):
            reason = _closed_young_import_denial(node, name, path, names)
            if reason:
                return reason, ()
            imports.append(node)
            continue
        fields = _RAW_CHILD_FIELDS.get(kind)
        if fields is None:
            return "unverified source syntax: " + kind, ()
        if kind == "_Del":
            # Deleting a package attribute can make a later from-import load
            # an unlisted submodule. This first receipt does not prove mutation
            # or initialization order, so do not speculate about deletion.
            return "unverified import-provider deletion", ()
        if kind == "_Name":
            ident = node.ident
            if ident in ("__pcc_freestanding__", "__pcc_runtime_port__"):
                return "raw runtime marker", ()
            if ident in _OPEN_DOMAIN_NAMES or (
                ident.startswith("__") and ident not in _PLAIN_DUNDER_NAMES
            ):
                return "dynamic or foreign capability: " + ident, ()
        elif kind == "_Attr":
            if node.name.startswith("__") or node.name in _OPEN_DOMAIN_NAMES:
                return "reflective or foreign attribute: " + node.name, ()
        elif kind in ("_FuncDef", "_ClassDef"):
            if node.decorators:
                return "unverified source decorator", ()
            if node.type_params:
                return "unverified source type parameters", ()
        if kind in ("_FuncDef", "_For", "_With") and node.is_async:
            return "unverified asynchronous source", ()
        for field in fields:
            work.append(getattr(node, field))
    return "", imports


def _source_bindings(module):
    """Only unconditional module bindings justify an attribute import.

    A package's missing attribute may import a submodule. Do not silently call
    that a value export, or trust a binding hidden in an unexecuted branch.
    """
    names = set()
    for node in module.body:
        kind = type(node).__name__
        if kind in ("_FuncDef", "_ClassDef"):
            names.add(node.name)
        elif kind == "_Assign" and node.has_value:
            targets = [node.target]
            while targets:
                target = targets.pop()
                if type(target).__name__ == "_Name":
                    names.add(target.ident)
                elif type(target).__name__ in ("_Tuple", "_List"):
                    targets.extend(target.elems)
        elif kind == "_Import":
            for imported, alias in node.names:
                names.add(alias or imported.split(".")[0])
        elif kind == "_ImportFrom":
            for imported, alias in node.names:
                names.add(alias or imported)
        elif kind in ("_If", "_While", "_For", "_Try", "_With", "_Del"):
            # Conservatively forget bindings across possible mutation/deletion.
            names.clear()
    return names


def _syntax_value(node, source_path, *, declarations=False):
    """Encode syntax, optionally retaining every pre-inference AST field."""
    from pcc.frontends.python import py_ast

    if node is None or isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, bytes):
        return {"bytes": node.hex()}
    if isinstance(node, (tuple, list)):
        return [_syntax_value(item, source_path, declarations=declarations) for item in node]
    kind = type(node).__name__
    fields = PY_AST_FIELD_NAME_OVERRIDES.get(kind)
    if fields is None or type(node) is not getattr(py_ast, kind, None):
        raise ValueError("unverified typed syntax: " + kind)
    if kind == "SourceSpan":
        if _canonical_path(node.file) != source_path:
            raise ValueError("typed source path does not match certified source")
        return [kind, source_path, node.line, node.col, node.end_line, node.end_col]
    if kind == "BoolExpr" and not declarations:
        # _infer_expr flattens same-op boolean chains left-to-right, then
        # _build_balanced_typed_bool_expr replaces only their grouping/spans.
        # Canonicalize that one rewrite, never mixed operators, leaf syntax,
        # evaluation order, or the root span. The input fingerprint stays exact.
        if fields != ("span", "ty", "op", "left", "right") or node.op not in ("and", "or"):
            raise ValueError("unverified boolean expression shape")
        pending = [node]
        leaves = []
        while pending:
            current = pending.pop()
            if type(current) is py_ast.BoolExpr and current.op == node.op:
                pending.append(current.right)
                pending.append(current.left)
            else:
                leaves.append(_syntax_value(current, source_path))
        return [kind, ["span", _syntax_value(node.span, source_path)],
                ["op", node.op], ["ordered_values", leaves]]
    values = [kind]
    for field in fields:
        # Inference does not replace every expression type (lambda bodies are
        # one example). The pre-inference boundary must retain ALL fields;
        # otherwise a transported sidecar can change boxing/ownership or ABI.
        if not declarations and field in ("ty", "annotation", "return_ty", "is_method"):
            continue
        values.append([field, _syntax_value(
            getattr(node, field), source_path, declarations=declarations,
        )])
    return values


def _source_lifted_value(module, source_path):
    return _syntax_value(module, source_path, declarations=True)


def _typed_metadata_denial(module, names):
    from pcc.frontends.python import py_ast

    work = [module]
    seen = set()
    while work:
        node = work.pop()
        if node is None or isinstance(node, (str, int, float, bytes)):
            continue
        if isinstance(node, (tuple, list)):
            work.extend(node)
            continue
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        kind = type(node).__name__
        fields = PY_AST_FIELD_NAME_OVERRIDES.get(kind)
        if fields is None or type(node) is not getattr(py_ast, kind, None):
            return "unverified typed metadata: " + kind
        if kind == "RawPointerType":
            return "raw pointer typed metadata"
        if isinstance(node, py_ast.Type):
            if _closed_young_raw_namespace(node.name):
                return "raw compiler/runtime typed metadata"
            if isinstance(node, py_ast.ClassType) and node.module and node.module not in names:
                return "typed class provider outside source closure: " + node.module
            if kind == "DynType" and node.name.startswith("module:"):
                if node.name[len("module:"):] not in names:
                    return "typed module provider outside source closure"
        for field in fields:
            work.append(getattr(node, field))
    return ""



def _add_import_initialization_dependencies(dependencies, importer, provider):
    # Importing a.b.c first executes a and a.b. Source completeness alone
    # cannot certify their eventual exports while either is initializing.
    # Preserve explicit self-import rejection, but do not invent a self-edge
    # when a package imports its own child from its already running __init__.
    dependencies.add(provider)
    parts = provider.split(".")
    for count in range(1, len(parts)):
        parent = ".".join(parts[:count])
        if parent != importer:
            dependencies.add(parent)


def _prepare(src_paths, module_names, entry_module):
    from pcc.frontends.python import py_lift, py_parse
    from pcc.frontends.python.codegen.import_lowering import ImportLoweringMixin

    if not isinstance(src_paths, (tuple, list)) or not isinstance(module_names, (tuple, list)):
        return None, "source closure requires explicit path and module lists"
    if not src_paths or len(src_paths) != len(module_names):
        return None, "source closure path/module count mismatch"
    if any(not _closed_young_module_name(name) for name in module_names):
        return None, "invalid source module identity"
    if len(set(module_names)) != len(module_names):
        return None, "duplicate source module identity"
    reason = _emitted_namespace_denial(module_names)
    if reason:
        return None, reason
    if entry_module not in module_names:
        return None, "entry module is missing from source closure"
    if any(_closed_young_raw_namespace(name) for name in module_names):
        return None, "raw compiler/runtime module namespace"
    reason = _intercepted_provider_denial(module_names)
    if reason:
        return None, reason
    extension_resolver = ImportLoweringMixin()
    for name in module_names:
        reason = _native_extension_provider_denial(extension_resolver, name)
        if reason:
            return None, reason
    paths = [_canonical_path(path) for path in src_paths]
    if len(set(paths)) != len(paths):
        return None, "duplicate canonical source path"
    providers = dict(zip(module_names, paths))
    for name, path in providers.items():
        if not path.endswith(".py"):
            return None, "source provider is not a Python source: " + name
        parts = name.split(".")
        for count in range(1, len(parts)):
            parent = ".".join(parts[:count])
            parent_path = providers.get(parent)
            if parent_path is None or os.path.basename(parent_path) != "__init__.py":
                return None, "missing source package provider: " + parent
            directory = os.path.dirname(parent_path)
            remaining = parts[count:]
            expected = os.path.join(directory, *remaining)
            expected += "/__init__.py" if os.path.basename(path) == "__init__.py" else ".py"
            if _canonical_path(expected) != path:
                return None, "source package path mismatch: " + name
    records = []
    parsed = {}
    imports = {}
    file_identities = set()
    for name in sorted(providers):
        path = providers[name]
        if not os.path.isfile(path):
            return None, "missing regular source file: " + name
        with open(path, "rb") as source_file:
            info = os.fstat(source_file.fileno())
            identity = (info.st_dev, info.st_ino)
            if identity in file_identities:
                return None, "duplicate actual source file"
            file_identities.add(identity)
            source_bytes = source_file.read()
        source = source_bytes.decode("utf-8-sig")
        raw = py_parse.parse(source, filename=path)
        reason, module_imports = _source_denial(raw, name, path, providers)
        if reason:
            return None, name + ": " + reason
        # There is no shallow parser, token filtering or import-only fallback.
        lifted = py_lift.finish_lifted_module(py_lift.lift_module(raw, path, name))
        reason = closed_young_module_denial(lifted, closure_modules=providers, module_path=path)
        if reason:
            return None, name + ": " + reason
        reason = _typed_metadata_denial(lifted, providers)
        if reason:
            return None, name + ": " + reason
        parsed[name] = raw
        imports[name] = module_imports
        records.append({
            "name": name, "path": path,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "syntax_sha256": _digest(_syntax_value(lifted, path)),
            "untyped_sha256": _digest(_source_lifted_value(lifted, path)),
        })
    bindings = {name: _source_bindings(raw) for name, raw in parsed.items()}
    dependencies = {name: set() for name in providers}
    for name, module_imports in imports.items():
        for node in module_imports:
            if type(node).__name__ == "_Import":
                for imported, _alias in node.names:
                    _add_import_initialization_dependencies(dependencies[name], name, imported)
                continue
            provider, reason = _closed_young_import_provider(node, name, providers[name])
            if reason:
                return None, reason
            _add_import_initialization_dependencies(dependencies[name], name, provider)
            for imported, _alias in node.names:
                # Codegen probes this candidate before existing value exports
                # and supplied Python submodules. Checking only the from-base
                # or unresolved names would certify the wrong provider.
                reason = _native_extension_provider_denial(
                    extension_resolver, provider + "." + imported,
                )
                if reason:
                    return None, reason
                if imported not in bindings[provider] and provider + "." + imported not in providers:
                    return None, "unresolved from-import provider: " + provider + "." + imported
                if provider + "." + imported in providers:
                    _add_import_initialization_dependencies(
                        dependencies[name], name, provider + "." + imported,
                    )
    # A partially initialized package may not have its eventual value exports
    # yet. Cyclic initialization needs a separate order proof; reject rather
    # than use those eventual bindings to bless a possible submodule fallback.
    remaining = dict(dependencies)
    while remaining:
        ready = {name for name, deps in remaining.items() if not deps.intersection(remaining)}
        if not ready:
            return None, "cyclic source imports require an initialization-order proof"
        for name in ready:
            del remaining[name]
    proof = {"schema": _SCHEMA, "version": _VERSION,
             "entry_module": entry_module, "modules": records}
    proof["digest"] = _digest(proof)
    return proof, ""


def prepare_closed_young_closure(src_paths, module_names, entry_module):
    """Return a reconstructible source receipt, or a normal denial reason."""
    try:
        return _prepare(src_paths, module_names, entry_module)
    except Exception as exc:
        # Unsupported parsing/metadata and vanished files decline optimization.
        # They must not prevent the ordinary compilation route from diagnosing
        # or handling the source using its unchanged behavior.
        return None, "source closure could not be proven: " + type(exc).__name__ + ": " + str(exc)


def _proof_shape_denial(proof):
    if type(proof) is not dict or set(proof) != _PROOF_KEYS:
        return "malformed source closure proof"
    if proof["schema"] != _SCHEMA or type(proof["version"]) is not int or proof["version"] != _VERSION:
        return "unsupported source closure proof schema/version"
    if not _closed_young_module_name(proof["entry_module"]):
        return "malformed proof entry module"
    if type(proof["modules"]) is not list or not proof["modules"]:
        return "malformed proof module records"
    for record in proof["modules"]:
        if type(record) is not dict or set(record) != _MODULE_KEYS:
            return "malformed proof source record"
        if not _closed_young_module_name(record["name"]) or type(record["path"]) is not str:
            return "malformed proof source identity"
        if _canonical_path(record["path"]) != record["path"]:
            return "proof source path is not canonical"
        for field in ("sha256", "syntax_sha256", "untyped_sha256"):
            value = record[field]
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                return "malformed proof source digest"
    unsigned = {key: value for key, value in proof.items() if key != "digest"}
    if proof["digest"] != _digest(unsigned):
        return "source closure proof digest mismatch"
    return ""


def validate_closed_young_closure(proof, src_paths, module_names, entry_module):
    """Reconstruct the receipt from the exact requested, current source set."""
    try:
        reason = _proof_shape_denial(proof)
        if reason:
            return reason
        actual, reason = prepare_closed_young_closure(src_paths, module_names, entry_module)
        if reason:
            return reason
        if proof != actual:
            return "source closure proof does not match current sources/providers/entry"
        return ""
    except Exception as exc:
        return "invalid source closure proof: " + type(exc).__name__


def closed_young_untyped_module_denial(proof, module, source_path):
    """Bind every field of the actual pre-inference AST, including Expr.ty."""
    try:
        reason = _proof_shape_denial(proof)
        if reason:
            return reason
        records = proof["modules"]
        names = [record["name"] for record in records]
        paths = [record["path"] for record in records]
        reason = validate_closed_young_closure(proof, paths, names, proof["entry_module"])
        if reason:
            return reason
        path = _canonical_path(source_path)
        matches = [record for record in records if record["name"] == module.name and record["path"] == path]
        if len(matches) != 1:
            return "untyped module identity is not in certified source closure"
        reason = closed_young_module_denial(module, closure_modules=names, module_path=path)
        if reason:
            return reason
        reason = _typed_metadata_denial(module, names)
        if reason:
            return reason
        if _digest(_source_lifted_value(module, path)) != matches[0]["untyped_sha256"]:
            return "untyped syntax/declarations do not match certified source"
        return ""
    except Exception as exc:
        return "untyped source closure could not be proven: " + type(exc).__name__ + ": " + str(exc)


def closed_young_typed_module_denial(proof, module, source_path):
    """Check capabilities and inference-stable syntax, not full type equivalence.

    The caller must first check its actual input with the untyped gate, bind
    shared inference inputs separately, and retain this exact typed object.
    """
    try:
        reason = _proof_shape_denial(proof)
        if reason:
            return reason
        records = proof["modules"]
        names = [record["name"] for record in records]
        paths = [record["path"] for record in records]
        reason = validate_closed_young_closure(proof, paths, names, proof["entry_module"])
        if reason:
            return reason
        path = _canonical_path(source_path)
        matches = [record for record in records if record["name"] == module.name and record["path"] == path]
        if len(matches) != 1:
            return "typed module identity is not in certified source closure"
        reason = closed_young_module_denial(module, closure_modules=names, module_path=path)
        if reason:
            return reason
        reason = _typed_metadata_denial(module, names)
        if reason:
            return reason
        if _digest(_syntax_value(module, path)) != matches[0]["syntax_sha256"]:
            return "typed syntax does not match certified source"
        return ""
    except Exception as exc:
        return "typed source closure could not be proven: " + type(exc).__name__ + ": " + str(exc)


def _transport_file(path):
    canonical = _canonical_path(path)
    if not os.path.isfile(canonical):
        raise ValueError("missing regular transport file: " + canonical)
    with open(canonical, "rb") as stream:
        contents = stream.read()
    return {"path": canonical, "sha256": hashlib.sha256(contents).hexdigest()}, contents


def _prepare_transport(exports_path, ast_dir, assigned_indices, *, snapshots=None):
    from pcc.frontends.python import pipeline_ast_wire, pipeline_exports, py_ast

    if not isinstance(assigned_indices, (tuple, list)) or not assigned_indices:
        return None, "transport requires explicit nonempty assigned indices"
    if any(type(index) is not int or index < 0 for index in assigned_indices):
        return None, "invalid transport assigned index"
    if len(set(assigned_indices)) != len(assigned_indices):
        return None, "duplicate transport assigned index"
    exports = None
    exports_text = None
    ast_texts = {}
    if exports_path is not None and exports_path != "":
        exports, contents = _transport_file(exports_path)
        text = contents.decode("utf-8")
        # Both production readers consume this one complete file. Indexed v1
        # stores every metadata, preload and module shard inline (D/F/P/T/G/
        # C/H/U/M rows); there are no external member files to leave unhashed.
        # A future external-member format needs a different transport proof.
        if text.startswith(pipeline_exports._NATIVE_EXPORT_INDEXED_SCHEMA + "\n"):
            wire_schema = pipeline_exports._NATIVE_EXPORT_INDEXED_SCHEMA
            if wire_schema != "pcc.frontends.python.native_exports.indexed.v1":
                return None, "unsupported indexed export transport schema"
            pipeline_exports._indexed_native_export_rows(text)
        else:
            payload = json.loads(text)
            wire_schema = payload.get("schema") if isinstance(payload, dict) else None
            if (wire_schema != pipeline_exports._NATIVE_EXPORT_WIRE_SCHEMA
                    or wire_schema != "pcc.frontends.python.native_exports.v1"):
                return None, "unsupported export transport schema"
            if not set(payload).issubset({
                "schema", "native_exports", "derived_class_map", "function_object_uses",
            }):
                return None, "unsupported export transport members"
        # Decode precisely the bytes hashed above, not a second path read.
        native, derived, uses = pipeline_exports._decode_native_exports_wire(text, True)
        if not isinstance(native, dict) or not isinstance(derived, dict) or not isinstance(uses, tuple):
            return None, "malformed export transport payload"
        exports["wire_schema"] = wire_schema
        exports_text = text
    directory = ""
    asts = []
    if ast_dir is not None and ast_dir != "":
        if exports is None:
            return None, "AST sidecar transport requires an export transport"
        directory = _canonical_path(ast_dir)
        if not os.path.isdir(directory):
            return None, "missing AST transport directory"
        seen_paths = set()
        for index in assigned_indices:
            record, contents = _transport_file(
                os.path.join(directory, "module_" + str(index) + ".json"),
            )
            if record["path"] in seen_paths or record["path"] == exports["path"]:
                return None, "duplicate transport artifact path"
            seen_paths.add(record["path"])
            ast_text = contents.decode("utf-8")
            payload = json.loads(ast_text)
            if (not isinstance(payload, dict) or set(payload) != {"schema", "module"}
                    or payload["schema"] != pipeline_ast_wire._PY_AST_WIRE_SCHEMA
                    or payload["schema"] != "pcc.frontends.python.py_ast.v1"):
                return None, "unsupported AST transport schema/members"
            if type(pipeline_ast_wire._decode_py_ast_wire(ast_text, record["path"])) is not py_ast.Module:
                return None, "AST transport does not contain a module"
            record["index"] = index
            asts.append(record)
            ast_texts[index] = ast_text
    token = {
        "schema": _TRANSPORT_SCHEMA, "version": _TRANSPORT_VERSION,
        "exports": exports, "ast_dir": directory,
        "assigned_indices": list(assigned_indices), "asts": asts,
    }
    token["digest"] = _digest(token)
    if snapshots is not None:
        snapshots["exports_text"] = exports_text
        snapshots["ast_texts"] = ast_texts
    return token, ""


def prepare_closed_young_transport(exports_path, ast_dir, assigned_indices):
    """Bind producer-owned exports and assigned AST sidecars to exact bytes.

    This is transport identity, not a proof that exports equal source semantics
    or that inference is type-equivalent. Source/pre-inference checks remain
    mandatory. Empty paths describe source-rebuilt inputs; an exports-only
    token describes workers that parse their assigned sources themselves.
    """
    try:
        return _prepare_transport(exports_path, ast_dir, assigned_indices)
    except Exception as exc:
        return None, "transport could not be proven: " + type(exc).__name__ + ": " + str(exc)


def load_closed_young_transport(token, exports_path, ast_dir, assigned_indices):
    """Return ((export text or None, index-to-AST-text dict), denial).

    Consumers must decode these verified snapshots, not reopen their paths.
    A hash before/after a separate read cannot prevent a replacement/ABA race.
    Text comes from exactly the byte buffers hashed into the compared token.
    """
    try:
        if type(token) is not dict or set(token) != _TRANSPORT_KEYS:
            return None, "malformed source closure transport token"
        if (token["schema"] != _TRANSPORT_SCHEMA
                or type(token["version"]) is not int
                or token["version"] != _TRANSPORT_VERSION):
            return None, "unsupported source closure transport schema/version"
        unsigned = {key: value for key, value in token.items() if key != "digest"}
        if token["digest"] != _digest(unsigned):
            return None, "source closure transport digest mismatch"
        snapshots = {}
        actual, reason = _prepare_transport(
            exports_path, ast_dir, assigned_indices, snapshots=snapshots,
        )
        if reason:
            return None, reason
        if token != actual:
            return None, "source closure transport does not match current artifacts/assignment"
        return (snapshots["exports_text"], snapshots["ast_texts"]), ""
    except Exception as exc:
        return None, "invalid source closure transport: " + type(exc).__name__


def validate_closed_young_transport(token, exports_path, ast_dir, assigned_indices):
    """Check current identities; actual consumers must use the load snapshots."""
    _snapshots, reason = load_closed_young_transport(token, exports_path, ast_dir, assigned_indices)
    return reason

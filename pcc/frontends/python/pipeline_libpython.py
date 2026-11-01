"""Import analysis for the native Python pipeline.

``ir_needs_libpython`` still reports generated calls into the retired
``py_cpy_*`` CPython bridge; the pipeline rejects such a module.
"""

from __future__ import annotations

from pcc.frontends.python import pipeline_import_policy as import_policy
from pcc.frontends.python.codegen.layer1_support import _dataclass_field_names
from pcc.frontends.python.pipeline_packages import resolve_pcc_native_extension_path
from pcc.driver.paths import join_dotted_parts


def ast_field_names(obj):
    return tuple(_dataclass_field_names(obj))


def ast_field_value(obj, field_name, default=None):
    return getattr(obj, field_name, default)


def ast_name_used_at_runtime(stmts, ident: str) -> bool:
    from pcc.frontends.python.py_ast import Import, ImportFrom, Name, Type

    annotation_slots = {"annotation", "return_ty"}
    pending = [stmts]
    while pending:
        item = pending.pop()
        if item is None or isinstance(item, Type) or isinstance(item, (Import, ImportFrom)):
            continue
        if isinstance(item, tuple):
            for child in item:
                pending.append(child)
            continue
        if isinstance(item, Name):
            if ast_field_value(item, "ident", "") == ident:
                return True
            continue
        for slot in ast_field_names(item):
            if slot not in annotation_slots:
                pending.append(ast_field_value(item, slot, None))
    return False


def module_import_is_scaffold(
    module_name: str | None,
    *,
    ir_scaffold_mode: str,
    current_module: str,
) -> bool:
    if module_name == "pcc.ir.compat":
        return (
            ir_scaffold_mode == "on"
            or current_module == "pcc.frontends.python.codegen.runtime_abi"
        )
    return module_name in import_policy.SCAFFOLD_IMPORT_MODULES


def resolve_relative_import(module, level, cur_parts):
    if not level:
        return module or ""
    if level > len(cur_parts):
        return module or ""
    base = cur_parts[: len(cur_parts) - level]
    if module:
        return ".".join(base + [module])
    return ".".join(base)


def module_imports_native_extension(
    ast_module,
    *,
    native_modules=None,
    ir_scaffold_mode: str = "off",
) -> bool:
    from pcc.frontends.python.py_ast import Import, ImportFrom

    native_set = set(native_modules or ())
    cur_mod = ast_field_value(ast_module, "name", "") or ""
    cur_parts = cur_mod.split(".") if cur_mod else []
    pending_stmts = [ast_field_value(ast_module, "body", ())]
    while pending_stmts:
        stmts = pending_stmts.pop()
        for stmt in stmts:
            if isinstance(stmt, ImportFrom):
                stmt_module = ast_field_value(stmt, "module", None)
                stmt_level = ast_field_value(stmt, "level", 0) or 0
                if module_import_is_scaffold(
                    stmt_module,
                    ir_scaffold_mode=ir_scaffold_mode,
                    current_module=cur_mod,
                ):
                    continue
                resolved = resolve_relative_import(stmt_module, stmt_level, cur_parts)
                if (
                    resolved not in native_set
                    and resolve_pcc_native_extension_path(resolved) is not None
                ):
                    return True
                for alias_name, _ in ast_field_value(stmt, "names", ()):
                    candidate = join_dotted_parts([resolved, alias_name])
                    if (
                        candidate not in native_set
                        and resolve_pcc_native_extension_path(candidate) is not None
                    ):
                        return True
            elif isinstance(stmt, Import):
                for module_name, _ in ast_field_value(stmt, "names", ()):
                    if (
                        module_name not in native_set
                        and resolve_pcc_native_extension_path(module_name) is not None
                    ):
                        return True
            for slot in ("body", "else_body", "finally_body"):
                body = ast_field_value(stmt, slot, None)
                if body:
                    pending_stmts.append(body)
            handlers = ast_field_value(stmt, "handlers", None)
            if handlers:
                for handler in handlers:
                    pending_stmts.append(ast_field_value(handler, "body", ()))
    return False


def ir_needs_libpython(ir_text: str) -> bool:
    if "@py_cpy_" not in ir_text:
        return False
    for line in ir_text.splitlines():
        if "@py_cpy_" not in line:
            continue
        stripped = line.lstrip()
        if (
            stripped.startswith("call ")
            or stripped.startswith("tail call ")
            or " = call " in line
            or " = tail call " in line
        ):
            return True
    return False

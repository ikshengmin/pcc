#!/usr/bin/env python3
"""Validate specification features, not test-outcome percentages.

A metadata layer over existing language/project/conformance tests. This module
does not compile programs, fetch suites, or certify execution owners.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

STATUSES = ("supported", "explicitly_unimplemented", "missing")
KINDS = {"unit", "program", "differential"}
OUTCOMES = {"passed", "failed", "blocked", "not_run"}
AXES = ("mode", "platform", "backend", "gc")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})\Z")


class LedgerError(ValueError):
    """Invalid or unsupported evidence: fail closed rather than invent coverage."""


def require(condition, message):
    if not condition:
        raise LedgerError(message)


def nonempty(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name}: nonempty string required")


def unique(rows, name, key="id"):
    require(isinstance(rows, list), f"{name}: list required")
    result = {}
    for row in rows:
        require(isinstance(row, dict), f"{name}: object required")
        nonempty(row.get(key), f"{name}.{key}")
        require(row[key] not in result, f"{name}: duplicate {row[key]}")
        result[row[key]] = row
    return result


def string_list(value, name):
    require(isinstance(value, list), f"{name}: list required")
    for item in value:
        nonempty(item, name)
    require(len(value) == len(set(value)), f"{name}: duplicates")


def source_identity(value, name, root=None):
    require(isinstance(value, dict), f"{name}: source object required")
    nonempty(value.get("path"), f"{name}.path")
    path = PurePosixPath(value["path"])
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in value["path"],
            f"{name}: repository-relative path required")
    require(bool(SHA256.fullmatch(value.get("sha256", ""))), f"{name}: sha256 required")
    if root is not None:
        candidate = (root / path).resolve()
        require(candidate.is_relative_to(root.resolve()), f"{name}: path escapes source root")
        require(candidate.is_file(), f"{name}: source file absent: {path}")
        require(hashlib.sha256(candidate.read_bytes()).hexdigest() == value["sha256"],
                f"{name}: source hash mismatch: {path}")


def process_result(value, name):
    require(isinstance(value, dict), f"{name}: process result required")
    require(type(value.get("exit_code")) is int, f"{name}: integer exit_code required")
    for stream in ("stdout_sha256", "stderr_sha256"):
        require(bool(SHA256.fullmatch(value.get(stream, ""))), f"{name}.{stream}: sha256 required")


def same_result(actual, expected):
    return all(actual[key] == expected[key]
               for key in ("exit_code", "stdout_sha256", "stderr_sha256"))


def execution_axes(value, name):
    require(value.get("mode") in ("pcc0", "pcc1"), f"{name}: mode must be pcc0 or pcc1")
    for key in AXES[1:]:
        nonempty(value.get(key), f"{name}.{key}")


def reference_identity(value, name):
    require(isinstance(value, dict), f"{name}: reference object required")
    for key in ("name", "version", "platform"):
        nonempty(value.get(key), f"{name}.{key}")
    require(value.get("role") == "external_reference", f"{name}: external_reference label required")
    require(value.get("kind") in ("native_execution", "metadata_typecheck", "expected_rejection"),
            f"{name}: reference kind required")


def validate_evidence(row, root=None):
    name = "evidence " + row["id"]
    nonempty(row.get("test_id"), name + ".test_id")
    require(row.get("kind") in KINDS, name + ": invalid kind")
    require(row.get("purpose") in ("unit", "runtime", "typecheck", "rejection"),
            name + ": explicit evidence purpose required")
    require(row.get("outcome") in OUTCOMES, name + ": invalid outcome")
    require(bool(REVISION.fullmatch(row.get("source_revision", ""))), name + ": source revision required")
    source_identity(row.get("source"), name, root)
    execution_axes(row, name)
    require(isinstance(row.get("command"), list) and bool(row["command"]), name + ": command required")
    require(row.get("command_kind") in ("executed", "reproduction"), name + ": command_kind required")
    nonempty(row["command"][0], name + ".command[0]")
    require(all(isinstance(argument, str) for argument in row["command"]),
            name + ": command arguments must be strings")
    if row["outcome"] in ("passed", "failed"):
        process_result(row.get("actual"), name + ".actual")
    if row["kind"] == "unit" and row["outcome"] == "passed":
        require(row["actual"]["exit_code"] == 0, name + ": passed unit must exit zero")
    if row["kind"] in ("program", "differential"):
        require(bool(SHA256.fullmatch(row.get("input_sha256", ""))),
                name + ": exact original input_sha256 required")
    if row["kind"] == "differential" and row["outcome"] in ("passed", "failed"):
        reference_identity(row.get("reference"), name)
        require(row["reference"].get("input_sha256") == row["input_sha256"],
                name + ": reference original input hash mismatch")
        process_result(row.get("expected"), name + ".expected")
        if row["purpose"] == "runtime":
            comparison = row.get("comparison", {})
            require(comparison.get("kind") in ("raw_process_streams", "normalized_primitive_value"),
                    name + ": runtime comparison kind required")
            if comparison["kind"] == "normalized_primitive_value":
                nonempty(comparison.get("description"), name + ": normalization description")
                process_result(row.get("raw_reference"), name + ".raw_reference")
            require(row["reference"]["kind"] == "native_execution",
                    name + ": runtime differential requires executed native reference")
            require((row["outcome"] == "passed") == same_result(row["actual"], row["expected"]),
                    name + ": differential outcome disagrees with stdout/stderr/exit")
        elif row["outcome"] == "passed":
            # Diagnostics need not be byte-identical between different compilers.
            expected_success = row["purpose"] == "typecheck"
            require((row["actual"]["exit_code"] == 0) == expected_success and
                    (row["expected"]["exit_code"] == 0) == expected_success,
                    name + ": acceptance/rejection does not agree")


def validate_ledger(data, root=None):
    require(data.get("schema_version") == 1, "schema_version must be 1")
    nonempty(data.get("language"), "language")
    specification = data.get("specification", {})
    for key in ("title", "version", "url"):
        nonempty(specification.get(key), "specification." + key)
    inventory = data.get("inventory", {})
    for key in ("chapters_complete", "features_complete"):
        require(type(inventory.get(key)) is bool, "inventory." + key + ": boolean required")
    nonempty(inventory.get("notes"), "inventory.notes")
    chapters = unique(data.get("chapters"), "chapters")
    require(bool(chapters), "at least one specification chapter is required")
    for chapter in chapters.values():
        for key in ("title", "url"):
            nonempty(chapter.get(key), "chapter." + key)
    features = unique(data.get("features"), "features")
    evidence = unique(data.get("evidence"), "evidence")
    for row in evidence.values():
        validate_evidence(row, root)
    for feature in features.values():
        name = "feature " + feature["id"]
        for key in ("title", "scope"):
            nonempty(feature.get(key), name + "." + key)
        require(feature.get("status") in STATUSES, name + ": invalid feature status")
        for key in ("chapter_ids", "tests", "diagnostics", "evidence"):
            string_list(feature.get(key), name + "." + key)
        require(bool(feature["chapter_ids"]), name + ": specification chapter required")
        require(set(feature["chapter_ids"]) <= chapters.keys(), name + ": unknown chapter")
        require(set(feature["evidence"]) <= evidence.keys(), name + ": unknown evidence")
        selected = [evidence[key] for key in feature["evidence"]]
        require(all(row["test_id"] in feature["tests"] for row in selected),
                name + ": evidence test not listed")
        if feature["status"] == "supported":
            qualified = defaultdict(set)
            for row in selected:
                if row["outcome"] != "passed":
                    continue
                if row["kind"] == "differential" and row.get("comparison", {}).get("kind") != "raw_process_streams":
                    continue
                if row["kind"] == "unit" or row["purpose"] == "runtime":
                    qualified[tuple(row[key] for key in (*AXES, "source_revision"))].add(row["kind"])
            require(any(kinds >= KINDS for kinds in qualified.values()),
                    name + ": supported requires unit, runnable program and runtime differential in one execution mode")
        if "out_of_scope_reason" in feature:
            nonempty(feature["out_of_scope_reason"], name + ".out_of_scope_reason")
    diagnostics = data.get("diagnostics", {})
    require(type(diagnostics.get("inventory_complete")) is bool,
            "diagnostics.inventory_complete: boolean required")
    nonempty(diagnostics.get("notes"), "diagnostics.notes")
    codes = unique(diagnostics.get("codes"), "diagnostics.codes", "code")
    for code, row in codes.items():
        require(row.get("feature_id") in features, "diagnostic " + code + ": unknown feature")
        source_identity(row.get("source"), "diagnostic " + code, root)
        require(code in features[row["feature_id"]]["diagnostics"],
                "diagnostic " + code + ": missing reverse mapping")
    for feature in features.values():
        for code in feature["diagnostics"]:
            require(code in codes and codes[code]["feature_id"] == feature["id"],
                    "feature " + feature["id"] + ": unmapped diagnostic " + code)
    require(isinstance(diagnostics.get("message_only"), list),
            "diagnostics.message_only: list required")
    for row in diagnostics["message_only"]:
        nonempty(row.get("message"), "message_only.message")
        nonempty(row.get("reason"), "message_only.reason")
        source_identity(row.get("source"), "message_only", root)
        if "feature_id" in row:
            require(row["feature_id"] in features, "message_only: unknown feature")
    mapped_messages = {row.get("feature_id") for row in diagnostics["message_only"]}
    for feature in features.values():
        if feature["status"] == "explicitly_unimplemented":
            require(bool(feature["diagnostics"]) or feature["id"] in mapped_messages,
                    "feature " + feature["id"] + ": explicitly_unimplemented requires a mapped source-bound diagnostic")
    require(not diagnostics["inventory_complete"] or not diagnostics["message_only"],
            "stable diagnostic-code inventory cannot be complete while message-only gaps remain")
    return data



def scan_unsupported_codes(root, language):
    """Static finite-string inventory, including prefix helpers; never import code.

    Dynamic constructions that cannot be resolved are gaps, not invented codes.
    Scope is the language frontend Python source tree, recorded in the result.
    """
    require(re.fullmatch(r"[a-z][a-z0-9_]*", language) is not None, "invalid language path")
    directory = root / "pcc" / "frontends" / language
    require(directory.is_dir(), "diagnostic source tree absent: " + str(directory))
    prefix = language + ".unsupported."
    found, unresolved, files = set(), [], {}
    for path in sorted(directory.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        files[relative] = hashlib.sha256(raw).hexdigest()
        tree = ast.parse(raw, filename=relative)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value

        def scope(node):
            while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = parents.get(node)
            return node

        def strings(node, seen=frozenset()):
            if id(node) in seen:
                return None
            seen = seen | {id(node)}
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return {node.value}
            if isinstance(node, ast.Name):
                owner = scope(node)
                if owner is not None:
                    if node.id in {arg.arg for arg in (*owner.args.posonlyargs, *owner.args.args,
                                                        *owner.args.kwonlyargs)}:
                        return None
                    if any(isinstance(item, (ast.AugAssign, ast.NamedExpr))
                           and isinstance(item.target, ast.Name) and item.target.id == node.id
                           for item in ast.walk(owner)):
                        return None
                if isinstance(constants.get(node.id), str) and owner is None:
                    return {constants[node.id]}
                if owner is not None:
                    assignments = [item.value for item in ast.walk(owner)
                                   if isinstance(item, ast.Assign) and scope(item) is owner
                                   and any(isinstance(target, ast.Name) and target.id == node.id
                                           for target in item.targets)]
                    values = [strings(item, seen) for item in assignments]
                    if values and all(value is not None for value in values):
                        return set().union(*values)
                    if assignments:
                        return None
                if isinstance(constants.get(node.id), str):
                    return {constants[node.id]}
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                value = constants.get(node.value.id)
                if isinstance(value, dict) and all(isinstance(item, str) for item in value.values()):
                    return set(value.values())
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name)):
                value = constants.get(node.func.value.id)
                if isinstance(value, dict) and all(isinstance(item, str) for item in value.values()):
                    default = strings(node.args[1], seen) if len(node.args) > 1 else None
                    if default is not None:
                        return set(value.values()) | default
            if isinstance(node, ast.IfExp):
                left, right = strings(node.body, seen), strings(node.orelse, seen)
                if left is not None and right is not None:
                    return left | right
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                left, right = strings(node.left, seen), strings(node.right, seen)
                if left is not None and right is not None:
                    return {a + b for a in left for b in right}
            if isinstance(node, ast.JoinedStr):
                values = {""}
                for part in node.values:
                    more = strings(part.value if isinstance(part, ast.FormattedValue) else part, seen)
                    if more is None:
                        return None
                    values = {a + b for a in values for b in more}
                return values
            return None

        def constant_prefix(node):
            values = strings(node)
            if values is not None and len(values) == 1:
                return next(iter(values))
            if isinstance(node, ast.BinOp):
                left = constant_prefix(node.left)
                if isinstance(node.op, ast.Add) and strings(node.left) is not None:
                    return left + constant_prefix(node.right)
                return left
            if isinstance(node, ast.JoinedStr):
                prefix_ = ""
                for part in node.values:
                    if not isinstance(part, ast.Constant):
                        break
                    prefix_ += part.value
                return prefix_
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format"):
                return constant_prefix(node.func.value).split("{", 1)[0]
            return ""

        def string_expression(node):
            return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    or isinstance(node, (ast.BinOp, ast.JoinedStr))
                    or isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format")

        for expression in ast.walk(tree):
            if not string_expression(expression):
                continue
            parent = parents.get(expression)
            if (isinstance(parent, (ast.BinOp, ast.JoinedStr, ast.FormattedValue))
                    or isinstance(parent, ast.Attribute) and parent.attr == "format"):
                continue
            values = strings(expression)
            known_prefix = constant_prefix(expression)
            relevant = (known_prefix.startswith(prefix) or values is not None
                        and any(value.startswith(prefix) for value in values))
            if not relevant:
                continue
            function = scope(expression)
            resolved = values is not None
            # A small prefix helper with finite callers is statically enumerable.
            if (not resolved and isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add)
                    and strings(expression.left) == {prefix}
                    and isinstance(expression.right, ast.Name) and function is not None):
                parameters = [item.arg for item in function.args.args]
                if expression.right.id in parameters:
                    index = parameters.index(expression.right.id)
                    calls = [item for item in ast.walk(tree) if isinstance(item, ast.Call)
                             and isinstance(item.func, ast.Name) and item.func.id == function.name]
                    escaped = any(isinstance(item, ast.Name) and item.id == function.name
                                  and not (isinstance(parents.get(item), ast.Call)
                                           and parents[item].func is item)
                                  for item in ast.walk(tree))
                    resolved, values = bool(calls) and not escaped, set()
                    for call in calls:
                        arguments = strings(call.args[index]) if len(call.args) > index else None
                        if arguments is None:
                            resolved = False
                        else:
                            values.update(prefix + value for value in arguments)
            if values:
                found.update(value for value in values
                             if re.fullmatch(re.escape(prefix) + r"[a-z0-9_-]+(?:[.][a-z0-9_-]+)*", value))
            if not resolved or values == {prefix}:
                unresolved.append({"path": relative, "line": expression.lineno, "prefix": known_prefix})
    return {"scope": "pcc/frontends/" + language, "source_files": files,
            "codes": sorted(found), "unresolved_dynamic_sites": unresolved}


def audit_diagnostics(data, root):
    validate_ledger(data)
    observed = scan_unsupported_codes(root, data["language"])
    declared = {row["code"] for row in data["diagnostics"]["codes"]}
    missing = sorted(set(observed["codes"]) - declared)
    stale = sorted(declared - set(observed["codes"]))
    return {**observed, "unmapped_codes": missing, "unmapped_count": len(missing),
            "declared_not_found": stale,
            "code_inventory_complete": not missing and not stale and not observed["unresolved_dynamic_sites"],
            "stable_message_code_gaps": len(data["diagnostics"]["message_only"])}


def coverage(data):
    """Denominators are inventoried feature rows; chapters are never a proxy."""
    validate_ledger(data)
    rows = data["features"]

    def counts(items):
        counts_ = Counter(row["status"] for row in items)
        return {"denominator": len(items), **{status: counts_[status] for status in STATUSES},
                "supported_fraction": counts_["supported"] / len(items) if items else None}

    evidence = {row["id"]: row for row in data["evidence"]}
    def lanes(feature):
        grouped = defaultdict(set)
        for key in feature["evidence"]:
            row = evidence[key]
            if row["outcome"] != "passed":
                continue
            if row["kind"] == "differential" and row.get("comparison", {}).get("kind") != "raw_process_streams":
                continue
            if row["kind"] == "unit" or row["purpose"] == "runtime":
                grouped[tuple(row[axis] for axis in (*AXES, "source_revision"))].add(row["kind"])
        return [dict(zip((*AXES, "source_revision"), lane))
                for lane, kinds in sorted(grouped.items()) if kinds >= KINDS]

    return {"language": data["language"], "inventory": data["inventory"],
            "supported_meaning": "qualified only in the listed execution lanes, not all modes or platforms",
            "evidence_validation": "metadata_only; raw artifacts require separate receipt verification",
            "features": counts(rows), "chapters": [
                {"id": chapter["id"], "title": chapter["title"],
                 **counts([row for row in rows if chapter["id"] in row["chapter_ids"]])}
                for chapter in data["chapters"]],
            "diagnostics": {"inventory_complete": data["diagnostics"]["inventory_complete"],
                            "mapped_codes": len(data["diagnostics"]["codes"]),
                            "message_only_gaps": len(data["diagnostics"]["message_only"])},
            "qualified_scopes": [
                {"feature_id": row["id"], "scope": row["scope"], "evidence": row["evidence"],
                 "execution_lanes": lanes(row)}
                for row in rows if row["status"] == "supported"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledgers", nargs="+", type=Path)
    parser.add_argument("--verify-files", type=Path,
                        help="verify source hashes against this exact source tree")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--audit-diagnostics", type=Path, help="scan actual frontend source for unmapped unsupported codes")
    args = parser.parse_args(argv)
    try:
        reports = []
        for path in args.ledgers:
            data = validate_ledger(json.loads(path.read_text()), args.verify_files)
            report = coverage(data)
            if args.audit_diagnostics is not None:
                audit = audit_diagnostics(data, args.audit_diagnostics)
                require(not audit["unmapped_codes"], "unmapped actual unsupported codes: " + ", ".join(audit["unmapped_codes"]))
                require(not audit["declared_not_found"], "stale declared unsupported codes: " + ", ".join(audit["declared_not_found"]))
                if data["diagnostics"]["inventory_complete"]:
                    require(audit["code_inventory_complete"], "dynamic diagnostic inventory is incomplete")
                report["source_diagnostic_audit"] = audit
            reports.append(report)
        print(json.dumps(reports if args.report else {"validated": len(reports)}, indent=2))
    except (LedgerError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"ledger validation failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Developer-only libclang AST inventory; never part of the owned compiler chain.

No source transformation or missing-header stubs are used. An AST recovered
after errors is explicitly partial and must not be counted as a parse pass.
"""

from __future__ import annotations

import argparse
import collections
import ctypes as C
import hashlib
import json
from pathlib import Path


class CXString(C.Structure):
    _fields_ = [("data", C.c_void_p), ("private_flags", C.c_uint)]


class CXCursor(C.Structure):
    _fields_ = [("kind", C.c_uint), ("xdata", C.c_int), ("data", C.c_void_p * 3)]


class CXLocation(C.Structure):
    _fields_ = [("ptr_data", C.c_void_p * 2), ("int_data", C.c_uint)]


class CXRange(C.Structure):
    _fields_ = [("ptr_data", C.c_void_p * 2), ("begin", C.c_uint), ("end", C.c_uint)]


class CXToken(C.Structure):
    _fields_ = [("int_data", C.c_uint * 4), ("ptr_data", C.c_void_p)]


FEATURES = {
    "FunctionTemplate": "function_template",
    "ClassTemplate": "class_template",
    "ClassTemplatePartialSpecialization": "class_template_partial_specialization",
    "TemplateTypeParameter": "type_template_parameter",
    "NonTypeTemplateParameter": "non_type_template_parameter",
    "TemplateTemplateParameter": "template_template_parameter",
    "Namespace": "namespace",
    "NamespaceAlias": "namespace_alias",
    "UsingDirective": "using_directive",
    "UsingDeclaration": "using_declaration",
    "TypeAliasDecl": "type_alias",
    "C++ base class specifier": "inheritance",
    "CXXBaseSpecifier": "inheritance",
    "Constructor": "constructor",
    "CXXConstructor": "constructor",
    "Destructor": "destructor",
    "CXXDestructor": "destructor",
    "CXXMethod": "member_function",
    "ConversionFunction": "conversion_function",
    "LambdaExpr": "lambda",
    "CXXForRangeStmt": "range_for",
    "CXXStaticCastExpr": "static_cast",
    "CXXReinterpretCastExpr": "reinterpret_cast",
    "CXXConstCastExpr": "const_cast",
    "CXXDynamicCastExpr": "dynamic_cast",
    "CXXNewExpr": "new_expression",
    "CXXDeleteExpr": "delete_expression",
    "CXXTryStmt": "try_statement",
    "CXXCatchStmt": "catch_statement",
    "CXXThrowExpr": "throw_expression",
    "CXXNullPtrLiteralExpr": "nullptr",
    "StaticAssert": "static_assert",
}


class Clang:
    def __init__(self, library: str):
        self.lib = C.CDLL(library)
        self.bind("clang_getCString", C.c_char_p, CXString)
        self.bind("clang_disposeString", None, CXString)
        self.bind("clang_getClangVersion", CXString)
        self.bind("clang_createIndex", C.c_void_p, C.c_int, C.c_int)
        self.bind("clang_disposeIndex", None, C.c_void_p)
        self.bind("clang_parseTranslationUnit2", C.c_int, C.c_void_p, C.c_char_p,
                  C.POINTER(C.c_char_p), C.c_int, C.c_void_p, C.c_uint,
                  C.c_uint, C.POINTER(C.c_void_p))
        self.bind("clang_disposeTranslationUnit", None, C.c_void_p)
        self.bind("clang_getTranslationUnitCursor", CXCursor, C.c_void_p)
        self.bind("clang_getCursorKindSpelling", CXString, C.c_uint)
        self.bind("clang_getCursorSpelling", CXString, CXCursor)
        self.bind("clang_getCursorLocation", CXLocation, CXCursor)
        self.bind("clang_getCursorExtent", CXRange, CXCursor)
        self.bind("clang_tokenize", None, C.c_void_p, CXRange,
                  C.POINTER(C.POINTER(CXToken)), C.POINTER(C.c_uint))
        self.bind("clang_getTokenSpelling", CXString, C.c_void_p, CXToken)
        self.bind("clang_disposeTokens", None, C.c_void_p,
                  C.POINTER(CXToken), C.c_uint)
        self.bind("clang_getSpellingLocation", None, CXLocation,
                  C.POINTER(C.c_void_p), C.POINTER(C.c_uint),
                  C.POINTER(C.c_uint), C.POINTER(C.c_uint))
        self.bind("clang_getFileName", CXString, C.c_void_p)
        self.bind("clang_getNumDiagnostics", C.c_uint, C.c_void_p)
        self.bind("clang_getDiagnostic", C.c_void_p, C.c_void_p, C.c_uint)
        self.bind("clang_getDiagnosticSeverity", C.c_uint, C.c_void_p)
        self.bind("clang_defaultDiagnosticDisplayOptions", C.c_uint)
        self.bind("clang_formatDiagnostic", CXString, C.c_void_p, C.c_uint)
        self.bind("clang_disposeDiagnostic", None, C.c_void_p)
        self.visitor_type = C.CFUNCTYPE(C.c_uint, CXCursor, CXCursor, C.c_void_p)
        self.bind("clang_visitChildren", C.c_uint, CXCursor,
                  self.visitor_type, C.c_void_p)

    def bind(self, name, result, *args):
        fn = getattr(self.lib, name)
        fn.restype, fn.argtypes = result, args

    def string(self, value):
        try:
            text = self.lib.clang_getCString(value)
            return text.decode("utf-8", "replace") if text else ""
        finally:
            self.lib.clang_disposeString(value)

    def parse(self, source: Path, arguments: list[str], *, include_roots=()):
        source = source.resolve()
        roots = tuple(Path(root).resolve(strict=True) for root in include_roots)
        if any(not root.is_dir() for root in roots):
            raise ValueError("include roots must be existing directories")
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        encoded = [arg.encode() for arg in arguments]
        argv = (C.c_char_p * len(encoded))(*encoded)
        index = self.lib.clang_createIndex(0, 0)
        if not index:
            raise RuntimeError("libclang could not create an index")
        unit = C.c_void_p()
        try:
            # KeepGoing retains partial ASTs for failed preprocessing. Their
            # evidence remains conditional on diagnostics, never a success label.
            error = self.lib.clang_parseTranslationUnit2(
                index, str(source).encode(), argv, len(encoded), None, 0,
                0x200, C.byref(unit))
            result = {"schema": "pcc.developer-cpp-feature-inventory.v1",
                      "source": str(source), "source_sha256": source_digest,
                      "arguments": arguments,
                      "feature_scope": "active main-file AST nodes; included headers excluded",
                      "parse_error": error, "diagnostics": [],
                      "ast_status": "unavailable", "features": {}, "cursor_kinds": {}}
            observed_files = {str(source): source_digest}
            if roots:
                result.update(
                    feature_scope="active AST nodes in main file and explicit include roots",
                    include_roots=[str(root) for root in roots],
                    observed_files=observed_files, feature_sites={})
            # A non-null output does not override a failed parse API result.
            # Do not inspect an AST whose construction was reported unsuccessful.
            if error != 0 or not unit:
                return result
            for i in range(self.lib.clang_getNumDiagnostics(unit)):
                diagnostic = self.lib.clang_getDiagnostic(unit, i)
                try:
                    result["diagnostics"].append({
                        "severity": self.lib.clang_getDiagnosticSeverity(diagnostic),
                        "text": self.string(self.lib.clang_formatDiagnostic(
                            diagnostic, self.lib.clang_defaultDiagnosticDisplayOptions()))})
                finally:
                    self.lib.clang_disposeDiagnostic(diagnostic)
            has_errors = any(d["severity"] >= 3 for d in result["diagnostics"])
            result["ast_status"] = "partial" if has_errors else "parsed"
            kinds = collections.Counter()
            features = {}
            feature_sites = {}

            def tokens(cursor):
                data, size = C.POINTER(CXToken)(), C.c_uint()
                self.lib.clang_tokenize(unit, self.lib.clang_getCursorExtent(cursor),
                                        C.byref(data), C.byref(size))
                try:
                    return [self.string(self.lib.clang_getTokenSpelling(unit, data[i]))
                            for i in range(size.value)]
                finally:
                    self.lib.clang_disposeTokens(unit, data, size)

            def observe_cursor(cursor):
                file = C.c_void_p()
                line, column, offset = C.c_uint(), C.c_uint(), C.c_uint()
                self.lib.clang_getSpellingLocation(
                    self.lib.clang_getCursorLocation(cursor), C.byref(file),
                    C.byref(line), C.byref(column), C.byref(offset))
                filename = self.string(self.lib.clang_getFileName(file)) if file else ""
                if not filename:
                    return 1
                path = Path(filename).resolve()
                if path != source and not any(path.is_relative_to(root) for root in roots):
                    return 1  # System/external subtrees are outside the explicit scope.
                filename = str(path)
                if roots and filename not in observed_files:
                    observed_files[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
                kind = self.string(self.lib.clang_getCursorKindSpelling(cursor.kind))
                kinds[kind] += 1
                observed = []
                if kind in FEATURES:
                    observed.append(FEATURES[kind])
                spelling = self.string(self.lib.clang_getCursorSpelling(cursor))
                if spelling.startswith("operator") and kind in {
                    "CXXMethod", "FunctionDecl", "FunctionTemplate", "ConversionFunction"
                }:
                    observed.append("operator_overload")
                if kind == "IfStmt" and tokens(cursor)[:2] == ["if", "constexpr"]:
                    observed.append("if_constexpr")
                if kind in {"TemplateTypeParameter", "NonTypeTemplateParameter"}:
                    if "..." in tokens(cursor):
                        observed.append("parameter_pack")
                if kind == "VarDecl":
                    declaration = tokens(cursor)
                    prefix = declaration[:declaration.index("=")] if "=" in declaration else declaration
                    if "constexpr" in prefix:
                        observed.append("constexpr_variable")
                if kind == "TypeAliasDecl" and "decltype" in tokens(cursor):
                    observed.append("decltype_type_alias")
                for feature in observed:
                    row = features.setdefault(feature, {"nodes": 0, "locations": []})
                    row["nodes"] += 1
                    location = {"line": line.value, "column": column.value,
                                "spelling": spelling}
                    if roots:
                        location["file"] = filename
                        site = dict(location, kind=kind)
                        key = (filename, line.value, column.value, kind, spelling)
                        feature_sites.setdefault(feature, {})[key] = site
                    if len(row["locations"]) < 5:
                        row["locations"].append(location)
                return 2

            visitor_error = None

            def visit(cursor, parent, data):
                nonlocal visitor_error
                try:
                    return observe_cursor(cursor)
                except BaseException as exc:
                    # ctypes otherwise prints and swallows callback exceptions,
                    # allowing an incomplete scan to masquerade as a parse pass.
                    if visitor_error is None:
                        visitor_error = exc
                    return 0  # CXChildVisit_Break

            callback = self.visitor_type(visit)
            self.lib.clang_visitChildren(
                self.lib.clang_getTranslationUnitCursor(unit), callback, None)
            if visitor_error is not None:
                raise RuntimeError("AST feature collection failed") from visitor_error
            result["cursor_kinds"] = dict(sorted(kinds.items()))
            result["features"] = dict(sorted(features.items()))
            if roots:
                result["feature_sites"] = {
                    feature: [sites[key] for key in sorted(sites)]
                    for feature, sites in sorted(feature_sites.items())}
            for filename, digest in observed_files.items():
                if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != digest:
                    raise RuntimeError("source changed during AST inventory: " + filename)
            return result
        finally:
            if unit:
                self.lib.clang_disposeTranslationUnit(unit)
            self.lib.clang_disposeIndex(index)


def merge_reports(reports):
    """Union source sites, not AST-node counts or template instantiations."""
    if not reports:
        raise ValueError("at least one report is required")
    files, sites, units = {}, {}, []
    for report in reports:
        if not report.get("include_roots"):
            raise ValueError("merging requires reports made with explicit include roots")
        if report["ast_status"] not in {"parsed", "partial", "unavailable"}:
            raise ValueError("unknown AST status")
        units.append({key: report[key] for key in (
            "source", "source_sha256", "arguments", "include_roots", "ast_status")})
        for filename, digest in report["observed_files"].items():
            if filename in files and files[filename] != digest:
                raise ValueError("source identity changed across reports: " + filename)
            files[filename] = digest
        for feature, locations in report["feature_sites"].items():
            for site in locations:
                if site["file"] not in report["observed_files"]:
                    raise ValueError("feature site has no source identity")
                key = tuple(site[name] for name in ("file", "line", "column", "kind", "spelling"))
                sites.setdefault(feature, {})[key] = site
    statuses = collections.Counter(unit["ast_status"] for unit in units)
    status = "unavailable" if statuses["unavailable"] else "partial" if statuses["partial"] else "parsed"
    return {
        "schema": "pcc.developer-cpp-feature-inventory.merge.v1",
        "analysis_owner": "development-reference-libclang",
        "is_owned_pcc1_execution": False, "ast_status": status,
        "translation_unit_statuses": dict(statuses), "translation_units": units,
        "feature_scope": "union of observed source sites across translation units; not semantic coverage",
        "observed_files": dict(sorted(files.items())),
        "features": {feature: {
            "source_sites": len(locations),
            "files": len({site["file"] for site in locations.values()}),
            "locations": [locations[key] for key in sorted(locations)]}
            for feature, locations in sorted(sites.items())},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libclang")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--include-root", action="append", type=Path, default=[])
    parser.add_argument("--merge", nargs="+", type=Path, metavar="REPORT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.merge:
        if args.source or args.libclang or args.include_root or args.arguments:
            parser.error("--merge cannot be combined with parse arguments")
        inputs = [path.read_bytes() for path in args.merge]
        result = merge_reports([json.loads(data) for data in inputs])
        result["report_inputs"] = [
            {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in zip(args.merge, inputs)]
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"ast_status": result["ast_status"],
                          "translation_unit_statuses": result["translation_unit_statuses"]}))
        return 0 if result["ast_status"] == "parsed" else 2
    if not args.source or not args.libclang:
        parser.error("parsing requires --source and --libclang")
    clang = Clang(args.libclang)
    flags = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    result = clang.parse(args.source, flags, include_roots=args.include_root)
    result["analysis_owner"] = "development-reference-libclang"
    result["version"] = clang.string(clang.lib.clang_getClangVersion())
    result["is_owned_pcc1_execution"] = False
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"source": str(args.source), "ast_status": result["ast_status"],
                      "diagnostics": len(result["diagnostics"]),
                      "features": {k: v["nodes"] for k, v in result["features"].items()}}))
    return 0 if result["ast_status"] == "parsed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

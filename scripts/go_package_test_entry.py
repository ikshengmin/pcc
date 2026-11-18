"""Build a Go package-test entry from original test declarations, without running cmd/go.

This intentionally finite driver covers ordinary package tests, benchmarks, fuzz
targets and line-comment examples in a non-covered package with no custom
TestMain. Unsupported discovery shapes fail closed, rather than dropping a test
or claiming that the generated source is a successfully compiled test binary.
"""
from __future__ import annotations

import json
import re

from pcc.frontends.go.syntax import GoParser, PointerSyntax, TypeName, scan


def _matches(name, prefix):
    if not name.startswith(prefix) or len(name) == len(prefix):
        return name == prefix
    next_char = name[len(prefix)]
    if not next_char.isascii():
        raise ValueError("unsupported non-ASCII test entry name: " + name)
    return not ("a" <= next_char <= "z")


def _example_output(fn, source, tokens, names):
    # Only support a trailing group of complete // lines. Verify the declaration
    # and braces using the Go scanner: a raw literal containing "// Output:" is
    # not a comment. Earlier executable source must stay completely intact.
    pos = names[(fn.token.line, fn.token.column)]
    if [tok.kind for tok in tokens[pos + 1:pos + 4]] != ["(", ")", "{"]:
        raise ValueError("unsupported example declaration: " + fn.token.text)
    depth, end, last_code = 1, pos + 4, tokens[pos + 3]
    while depth:
        tok = tokens[end]
        if tok.kind == "eof":
            raise ValueError("unclosed example: " + fn.token.text)
        depth += (tok.kind == "{") - (tok.kind == "}")
        if depth and tok.kind != ";":
            last_code = tok
        end += 1
    # No comment on a code line may masquerade as the output group. Any
    # valid examples outside this bounded formatting are rejected, not omitted.
    lines = source.split("\n")
    close = tokens[end - 1]
    if lines[close.line - 1][:close.column - 1].strip():
        raise ValueError("unsupported inline example ending: " + fn.token.text)
    last_line = last_code.line + last_code.text.count("\n")
    upto = close.line - 2
    while upto >= last_line and not lines[upto].strip():
        upto -= 1
    group = []
    while upto >= last_line and lines[upto].lstrip(" \t").startswith("//"):
        value = lines[upto].lstrip(" \t")[2:].replace("\r", "")
        # go/ast.CommentGroup.Text omits directives before Output matching.
        # Do not transfer a directive into an expected output string.
        if value.startswith(("line ", "extern ", "export ")) or re.match(r"[a-z0-9]+:[a-z0-9]", value):
            raise ValueError("unsupported directive in example output: " + fn.token.text)
        group.append((value[1:] if value.startswith(" ") else value).rstrip(" \t"))
        upto -= 1
    group.reverse()
    if not group:
        raise ValueError("missing or unsupported example output: " + fn.token.text)
    match = re.fullmatch(r"(?i)(unordered )?output:(.*)", group[0])
    if match is None:
        raise ValueError("missing or unsupported example output: " + fn.token.text)
    group[0] = match.group(2).lstrip(" ")
    if not group[0]:
        group.pop(0)
    while group and not group[-1]:
        group.pop()
    if any(not line for line in group):
        raise ValueError("unsupported blank lines in example output: " + fn.token.text)
    return "\n".join(group) + ("\n" if group else ""), bool(match.group(1))


def build_entry(package, internal, external, *, module_path=""):
    """Return the driver source and its registered entries in source order."""
    if not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", package) or any(part in ("", ".", "..") for part in package.split("/")) or not isinstance(module_path, str):
        raise ValueError("invalid package import path")
    rows = {key: [] for key in ("tests", "benchmarks", "fuzzTargets", "examples")}
    group_used = {}
    seen = set()
    for owner, units in (("_test", tuple(internal)), ("_xtest", tuple(external))):
        used, packages = False, set()
        for filename, source in units:
            if not filename.endswith("_test.go"):
                raise ValueError("test entry requires original _test.go files")
            program = GoParser().parse(source, filename, require_main=False)
            packages.add(program.token.text)
            tokens = scan(source, filename)
            token_names = {(token.line, token.column): index for index, token in enumerate(tokens)
                           if token.kind == "name"}
            for fn in program.functions:
                if fn.receiver is not None:
                    continue
                name = fn.token.text
                for prefix, group, dtype in (("Test", "tests", "T"), ("Benchmark", "benchmarks", "B"),
                                             ("Fuzz", "fuzzTargets", "F"), ("Example", "examples", "")):
                    if _matches(name, prefix):
                        break
                else:
                    continue
                if fn.type_parameters or fn.result != "void":
                    raise ValueError("unsupported test signature: " + name)
                if name in seen:
                    raise ValueError("duplicate test entry: " + name)
                seen.add(name)
                if dtype:
                    param = fn.params[0][1] if len(fn.params) == 1 else None
                    if not isinstance(param, PointerSyntax) or not isinstance(param.element, TypeName):
                        raise ValueError("invalid testing parameter: " + name)
                    typ = param.element
                    allowed = {item.alias or "testing" for item in program.imports if item.path == "testing"}
                    if name == "TestMain" and typ.token.text == "M":
                        raise ValueError("custom TestMain is not supported")
                    if typ.token.text != dtype or (typ.qualifier or ".") not in allowed:
                        raise ValueError("invalid testing parameter: " + name)
                    rows[group].append((name, owner, "", False))
                else:
                    if fn.params:
                        raise ValueError("unsupported example parameters: " + name)
                    output, unordered = _example_output(fn, source, tokens, token_names)
                    rows[group].append((name, owner, output, unordered))
                used = True
        if len(packages) > 1:
            raise ValueError("test source package names differ")
        group_used[owner] = (bool(units), used, next(iter(packages), ""))
    inside, outside = group_used["_test"][2], group_used["_xtest"][2]
    if inside and outside and outside != inside + "_test":
        raise ValueError("external test package name differs")

    # The registration calls are the Go 1.27 test entry ABI. No runtime,
    # TestDeps object or example-output behavior is implemented by this helper.
    q = lambda text: json.dumps(text, ensure_ascii=False)
    code = ["package main", "", 'import (', '\t"os"', '\t"testing"', '\t"testing/internal/testdeps"']
    for owner, path in (("_test", package), ("_xtest", package + "_test")):
        present, needed, _ = group_used[owner]
        if present:
            code.append("\t" + (owner if needed else "_") + " " + q(path))
    code.extend([")", ""])
    for group, typ in (("tests", "InternalTest"), ("benchmarks", "InternalBenchmark"),
                       ("fuzzTargets", "InternalFuzzTarget"), ("examples", "InternalExample")):
        code.append("var " + group + " = []testing." + typ + "{")
        for name, owner, output, unordered in rows[group]:
            fields = [q(name), owner + "." + name]
            if group == "examples":
                fields += [q(output), "true" if unordered else "false"]
            code.append("\t{" + ", ".join(fields) + "},")
        code.extend(["}", ""])
    code.extend(["func init() {", "\ttestdeps.ModulePath = " + q(module_path),
                 "\ttestdeps.ImportPath = " + q(package), "}", "",
                 "func main() {", "\tm := testing.MainStart(testdeps.TestDeps{}, tests, benchmarks, fuzzTargets, examples)",
                 "\tos.Exit(m.Run())", "}", ""])
    return "\n".join(code), rows

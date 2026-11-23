"""Remove the declarations of runtime symbols that no longer exist.

A symbol or global word counts as removed when the committed runtime
defined it and the working tree does not. Their entries leave the ABI
tables and name sets of runtime_abi.py, their prototypes leave the C
headers, deleted modules leave the Makefile, and words nothing names any
more leave freestanding_gc_state.py.

    cleanup_declarations.py report | apply
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
(REPO / "build" / "one-collector").mkdir(parents=True, exist_ok=True)
RUNTIME = REPO / "pcc" / "runtime" / "py"
ABI = REPO / "pcc" / "frontends" / "python" / "codegen" / "runtime_abi.py"
HEADERS = [REPO / "pcc/runtime/include/py_runtime.h", REPO / "pcc/runtime/src/py_internal.h"]
MAKEFILE = REPO / "pcc/runtime/Makefile"
STATE = RUNTIME / "freestanding_gc_state.py"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
EXPORT = re.compile(r'@c_abi(?:_typed)?_export\(\s*"([A-Za-z0-9_]+)"')
GLOBAL = re.compile(r'define_(?:global|thread_local)_[a-z0-9_]+\(\s*"([A-Za-z0-9_]+)"')


def committed_runtime():
    listing = subprocess.run(["git", "-C", str(REPO), "ls-tree", "--name-only", "HEAD", "pcc/runtime/py/"],
                             capture_output=True, text=True, check=True).stdout.split()
    out = {}
    for relative in listing:
        if relative.endswith(".py"):
            out[relative] = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:" + relative],
                                           capture_output=True, text=True, check=True).stdout
    return out


def survey():
    before = committed_runtime()
    now = {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in sorted(RUNTIME.glob("*.py"))}
    symbols_before = set()
    globals_before = set()
    for text in before.values():
        symbols_before.update(EXPORT.findall(text))
        globals_before.update(GLOBAL.findall(text))
    symbols_now = set()
    globals_now = set()
    for text in now.values():
        symbols_now.update(EXPORT.findall(text))
        globals_now.update(GLOBAL.findall(text))
    removed_modules = sorted(Path(r).stem for r in before if r not in now)
    return symbols_before - symbols_now, globals_before - globals_now, removed_modules, now


def unused_state_words(now) -> list:
    """Words defined in freestanding_gc_state.py that no source names any
    more: not the runtime, not the compiler, not a header."""
    text = STATE.read_text(encoding="utf-8")
    defined = GLOBAL.findall(text)
    named = set()
    for relative, source in now.items():
        ids = IDENT.findall(source)
        if relative.endswith("freestanding_gc_state.py"):
            # its own definitions do not count as uses
            counts = {}
            for ident in ids:
                counts[ident] = counts.get(ident, 0) + 1
            named.update(i for i, n in counts.items() if n > 1)
        else:
            named.update(ids)
    for root in (REPO / "pcc", REPO / "scripts", REPO / "benchmarks"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".h", ".c"):
                continue
            parts = path.relative_to(REPO).parts
            if "build_py" in parts or "build_owned" in parts or path.parent == RUNTIME or path == ABI:
                continue
            named.update(IDENT.findall(path.read_text(encoding="utf-8", errors="replace")))
    return [word for word in defined if word not in named and word.startswith(("pcc_gc_", "py_gc_"))]


def clean_abi(removed: set, apply: bool) -> int:
    source = ABI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    ranges = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
                node.name.startswith("_runtime_signatures_part_")
                or node.name.startswith("_cross_object_signatures_part_")):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    for key, value in zip(inner.keys, inner.values):
                        if isinstance(key, ast.Constant) and key.value in removed:
                            ranges.append((key.lineno, value.end_lineno))
    # elements of name sets and one-line attribute entries
    element = re.compile(r"""^\s*(['"])([A-Za-z0-9_]+)\1\s*(?::[^\n]*)?,\s*$""")
    covered = set()
    for first, last in ranges:
        covered.update(range(first, last + 1))
    for number, text in enumerate(lines, 1):
        if number in covered:
            continue
        match = element.match(text)
        if match and match.group(2) in removed:
            ranges.append((number, number))
    if apply and ranges:
        for first, last in sorted(set(ranges), reverse=True):
            del lines[first - 1:last]
        ABI.write_text("".join(lines), encoding="utf-8")
    return len(set(ranges))


def clean_headers(removed: set, apply: bool) -> int:
    total = 0
    for header in HEADERS:
        text = header.read_text(encoding="utf-8")
        count = 0
        for name in sorted(removed):
            if name not in text:
                continue
            pattern = re.compile(r"(?m)^[^\n#/{};]*\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*;[^\n]*\n")
            text, n = pattern.subn("", text)
            count += n
        total += count
        if apply and count:
            header.write_text(text, encoding="utf-8")
    return total


def clean_makefile(removed_modules, apply: bool) -> int:
    text = MAKEFILE.read_text(encoding="utf-8")
    count = 0
    for module in removed_modules:
        new, n = re.subn(r"(?<![A-Za-z0-9_])" + re.escape(module) + r"(?![A-Za-z0-9_])[ \t]?", "", text)
        if n:
            count += n
            text = new
    if apply and count:
        MAKEFILE.write_text(text, encoding="utf-8")
    return count


def clean_state(words, apply: bool) -> int:
    source = STATE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    ranges = []
    for statement in tree.body:
        if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id.startswith("define_")
                and statement.value.args and isinstance(statement.value.args[0], ast.Constant)
                and statement.value.args[0].value in words):
            ranges.append((statement.lineno, statement.end_lineno))
    if apply and ranges:
        for first, last in sorted(ranges, reverse=True):
            del lines[first - 1:last]
        STATE.write_text("".join(lines), encoding="utf-8")
    return len(ranges)


def main(argv) -> int:
    apply = len(argv) > 0 and argv[0] == "apply"
    removed_symbols, removed_globals, removed_modules, now = survey()
    words = unused_state_words(now)
    removed_names = set(removed_symbols) | set(removed_globals) | set(words)
    print("removed symbols:", len(removed_symbols), " removed words:", len(removed_globals),
          " removed modules:", len(removed_modules), " unused state words:", len(words))
    if apply:
        state = clean_state(set(words), True)
        removed_names |= set(words)
    else:
        state = clean_state(set(words), False)
    print("state words:", state)
    print("ABI entries:", clean_abi(removed_names, apply))
    print("header prototypes:", clean_headers(set(removed_symbols), apply))
    print("Makefile names:", clean_makefile(removed_modules, apply))
    json.dump({"symbols": sorted(removed_symbols), "globals": sorted(removed_globals | set(words)),
               "modules": removed_modules},
              open(REPO / "build/one-collector/removed.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Find the tests that exist only for what the one-collector work removed.

A test is dead when its own source, or a module-level name it uses, names a
removed runtime symbol or word, a deleted runtime module, or selects one of
collectors 1-4. A file whose tests are all dead is deleted; in a mixed file
only the dead tests go.

    prune_tests.py report | apply
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
(REPO / "build" / "one-collector").mkdir(parents=True, exist_ok=True)
removed = json.load(open(Path(__file__).with_name("removed.json")))
NAMES = set(removed["symbols"]) | set(removed["globals"]) | {m for m in removed["modules"]}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SELECT = re.compile(
    r"""PCC_GC_BACKEND["']?\s*[:=,]\s*["']?[1-4]\b|"""
    r"""pcc_gc_set_backend|gc_backend\s*=\s*[1-4]\b|backend\s*(?:==|in)\s*\(?[1-4]\b"""
)


def dead_text(text: str) -> bool:
    if SELECT.search(text):
        return True
    return not NAMES.isdisjoint(IDENT.findall(text))


def segment(lines, node) -> str:
    first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return "".join(lines[first - 1:node.end_lineno])


def classify(path: Path):
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.splitlines(keepends=True)
    # module-level names whose definition is dead (one level of use)
    dead_names = set()
    module_dead = False
    for statement in tree.body:
        text = segment(lines, statement)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not statement.name.startswith(("test_", "Test")) and dead_text(text):
                dead_names.add(statement.name)
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            if dead_text(text):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            dead_names.add(name.id)
        elif isinstance(statement, (ast.Expr, ast.If, ast.Try, ast.With, ast.For)):
            if dead_text(text) and not (isinstance(statement, ast.Expr)
                                         and isinstance(statement.value, ast.Constant)):
                module_dead = True
    # close over helpers that use dead helpers
    changed = True
    while changed:
        changed = False
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if statement.name in dead_names or statement.name.startswith(("test_", "Test")):
                    continue
                used = {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)}
                if used & dead_names:
                    dead_names.add(statement.name)
                    changed = True
    tests = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith("test_"):
            text = segment(lines, statement)
            used = {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)}
            tests.append((statement, module_dead or dead_text(text) or bool(used & dead_names)))
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            text = segment(lines, statement)
            used = {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)}
            tests.append((statement, module_dead or dead_text(text) or bool(used & dead_names)))
    return source, lines, tests, dead_names, module_dead


def main(argv) -> int:
    apply = len(argv) > 0 and argv[0] == "apply"
    whole, partial, helpers = [], [], []
    for path in sorted((REPO / "tests").rglob("*.py")):
        result = classify(path)
        if result is None:
            continue
        source, lines, tests, dead_names, module_dead = result
        if not tests:
            # a helper module: dead when its own text is
            if dead_text(source) and path.name not in ("conftest.py", "__init__.py"):
                helpers.append(path)
            continue
        dead = [t for t, is_dead in tests if is_dead]
        if not dead:
            continue
        if len(dead) == len(tests):
            whole.append(path)
        else:
            partial.append((path, dead, len(tests)))
    print("test files to delete:", len(whole))
    print("test files losing some tests:", len(partial), "tests:", sum(len(d) for _p, d, _n in partial))
    print("helper modules naming removed things (not touched):", len(helpers))
    json.dump({"whole": [str(p.relative_to(REPO)) for p in whole],
               "partial": {str(p.relative_to(REPO)): [t.name for t in d] for p, d, _n in partial},
               "helpers": [str(p.relative_to(REPO)) for p in helpers]},
              open(REPO / "build/one-collector/prune.json", "w"), indent=1)
    if not apply:
        return 0
    for path in whole:
        path.unlink()
    for path, dead, _count in partial:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for node in sorted(dead, key=lambda n: n.lineno, reverse=True):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            del lines[first - 1:node.end_lineno]
            index = first - 1
            while (index < len(lines) and index >= 2 and lines[index].strip() == ""
                   and lines[index - 1].strip() == "" and lines[index - 2].strip() == ""):
                del lines[index]
        text = "".join(lines).rstrip("\n") + "\n"
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""No-skip doctrine: tests run or are deselected, never skipped.

A skipped test reports green while proving nothing, so this repository routes
"cannot run here" through `pytest.mark.pcc_gate(unavailable=...)`, which
*deselects*. `pytest.skip` / `skipif` / `importorskip` / `unittest.SkipTest`
are banned in executed code (TEST-P1-NO-SKIP-DOCTRINE-REMAINING-FAMILIES).

This was a manual `rg` line in the task board's gate list, which means a human
had to eyeball every hit and decide whether it was real. Two kinds of hit are
legitimate and look identical to a text search:

- source-text fixtures — a test that feeds `"@pytest.mark.skipif(...)"` to the
  compiler as *input*
- the compiler's own recognition of those decorator names
  (`pcc/driver/cli_bootstrap.py`, `pcc/frontends/python/codegen/decorator_lowering.py`)

Parsing instead of grepping makes that distinction structural: a string
literal is not a call, so only genuinely executed skips are reported. A skip
mark counts wherever it is built: a decorator, a module-level ``pytestmark``
or ``pytest.param(..., marks=...)``.

One directory is exempt by exact path: ``scripts/cpython_conformance/support``
is the port of CPython's ``test.support``. Upstream tests declare an unmet
requirement through ``unittest.SkipTest`` and its decorators, and the
conformance runner records that as its own SKIP status, which never counts as
a success; ``test_upstream_protocol_skips_are_never_successes`` keeps that
condition checked.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("AGENTS.md not found above " + __file__)


REPO = _repo_root()
UPSTREAM_PROTOCOL = REPO / "scripts" / "cpython_conformance" / "support"
CONFORMANCE_RUN = REPO / "scripts" / "cpython_conformance" / "run.py"

_SKIP_CALLS = {
    ("pytest", "skip"),
    ("pytest", "importorskip"),
    ("unittest", "SkipTest"),
}
_SKIP_DECORATORS = {
    ("pytest", "mark", "skip"),
    ("pytest", "mark", "skipif"),
    ("unittest", "skip"),
    ("unittest", "skipUnless"),
    ("unittest", "skipIf"),
}


def _dotted(node) -> tuple[str, ...] | None:
    """Dotted name for `a.b.c`, or None for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return tuple(reversed(parts))


def _offenders_in_tree(tree: ast.AST, label: str) -> list[str]:
    found: list[str] = []
    called: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in _SKIP_CALLS:
                called.add(id(node.func))
                found.append(f"{label}:{node.lineno}: {'.'.join(name)}()")
            elif name in _SKIP_DECORATORS:
                called.add(id(node.func))
                found.append(f"{label}:{node.lineno}: {'.'.join(name)}(...)")
    # A bare mark (``@pytest.mark.skip``, ``marks=pytest.mark.skip``).
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and id(node) not in called:
            name = _dotted(node)
            if name in _SKIP_DECORATORS:
                found.append(f"{label}:{node.lineno}: {'.'.join(name)}")
    return found


def _offenders_in(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    return _offenders_in_tree(tree, str(path.relative_to(REPO)))


def _python_sources():
    for root in ("tests", "pcc", "scripts"):
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_no_executed_skip_mechanism_anywhere():
    offenders: list[str] = []
    for path in _python_sources():
        if UPSTREAM_PROTOCOL in path.parents:
            continue
        offenders.extend(_offenders_in(path))
    assert not offenders, (
        "the no-skip doctrine bans these: a skipped test reports green while "
        "proving nothing. Use pytest.mark.pcc_gate(unavailable=<reason>) so the "
        "test is deselected instead:\n  " + "\n  ".join(sorted(offenders))
    )


def test_upstream_protocol_skips_are_never_successes():
    """The exemption above holds only while a SKIP is not a success."""
    tree = ast.parse(CONFORMANCE_RUN.read_text(encoding="utf-8"))
    success = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SUCCESS" for t in node.targets)
    )
    assert "PASS" in success and "SKIP" not in success, success
    # The exemption is one directory, and it still holds the protocol.
    assert UPSTREAM_PROTOCOL.is_dir()
    assert _offenders_in(UPSTREAM_PROTOCOL / "__init__.py")


def test_the_scan_actually_reads_the_tree():
    """A scanner that parses nothing passes for the wrong reason."""
    count = sum(1 for _ in _python_sources())
    assert count > 500, count


def test_the_scan_would_catch_a_real_skip(tmp_path):
    """Positive control — the checks above are only worth their false-negative
    rate, so prove the scanner fires on each banned form."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pytest\n"
        "import unittest\n"
        "\n"
        "# a source-text fixture must NOT be reported\n"
        'FIXTURE = "@pytest.mark.skipif(True, reason=\'skip\')"\n'
        "RECOGNIZED = ('pytest.mark.skip', 'pytest.mark.skipif')\n"
        "\n"
        "def a():\n"
        "    pytest.skip('nope')\n"
        "\n"
        "@pytest.mark.skipif(True, reason='x')\n"
        "def b():\n"
        "    pass\n"
        "\n"
        "def c():\n"
        "    raise unittest.SkipTest('nope')\n"
        "\n"
        "pytestmark = [pytest.mark.skipif(True, reason='module')]\n"
        "\n"
        "@pytest.mark.skip\n"
        "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.skip)])\n"
        "def d(x):\n"
        "    pass\n"
        "\n"
        "@pytest.mark.pcc_gate(platforms=('linux',))\n"
        "def e():\n"
        "    pytest.fail('a gated test fails when reached')\n",
        encoding="utf-8",
    )
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    found = _offenders_in_tree(tree, "probe.py")
    lines = sorted(int(entry.split(":")[1]) for entry in found)
    # pytest.skip, the skipif decorator, unittest.SkipTest, the module mark,
    # the bare decorator and the param mark; never the string fixtures or the
    # pcc_gate/pytest.fail pair.
    assert lines == [9, 11, 16, 18, 20, 21], found

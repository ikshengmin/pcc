"""The Python edition of church-turing: checked, and run by Python itself."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

from pcc.proof.session import Session

from tests.proof.support import erased_normal_form, render_runtime_value

EDITIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editions")
PACKAGE = os.path.join(EDITIONS, "church_turing")
MODULES = ("lambda_de_bruijn", "lambda_untyped", "lambda_weak_head", "demo")


@pytest.fixture(scope="module")
def session():
    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous, 20000))
    try:
        checked = Session()
        for module in MODULES:
            checked.check_file(os.path.join(PACKAGE, module + ".py"))
        yield checked
    finally:
        sys.setrecursionlimit(previous)


@pytest.fixture(scope="module")
def running():
    """The edition imported by CPython, as any program would import it."""
    sys.path.insert(0, EDITIONS)
    try:
        yield {
            module: importlib.import_module(f"church_turing.{module}") for module in MODULES
        }
    finally:
        sys.path.remove(EDITIONS)
        for name in [name for name in sys.modules if name.split(".")[0] == "church_turing"]:
            del sys.modules[name]


def test_every_module_is_checked(session):
    counts = {
        module: len(session.reports[f"church_turing.{module}"][1]) for module in MODULES
    }
    assert counts == {
        "lambda_de_bruijn": 19,
        "lambda_untyped": 14,
        "lambda_weak_head": 19,
        "demo": 18,
    }
    assert session.kernel.idle


def test_the_relations_are_indexed_families(session):
    sig = session.kernel.sig
    step = sig.datas["church_turing.lambda_untyped.Step"]
    assert (step.nparams, step.nindices) == (0, 2)
    assert [name.rsplit(".", 1)[-1] for name in step.constructors] == [
        "Beta", "App1", "App2", "Under",
    ]
    steps = sig.datas["church_turing.lambda_untyped.Steps"]
    assert (steps.nparams, steps.nindices) == (0, 2)


def test_logic_and_python_compute_the_same_values(session, running):
    """Each closed constant: its kernel normal form is the value Python holds."""
    scope = session.modules["church_turing.demo"]
    constants = [
        name
        for name in session.reports["church_turing.demo"][1]
        if scope[name].kind == "function" and not scope[name].callable
    ]
    assert len(constants) == 14
    for name in constants:
        logical = erased_normal_form(session, "church_turing.demo", name)
        executed = render_runtime_value(getattr(running["demo"], name))
        assert logical == executed, name


def test_known_values(running):
    demo = running["demo"]
    terms = running["lambda_de_bruijn"]
    reduction = running["lambda_untyped"]
    assert demo.LIFTED == terms.App(terms.Var(1), terms.Lam(terms.Var(2)))
    assert demo.SUBSTITUTED == terms.App(terms.Lam(terms.Var(0)), terms.Var(0))
    assert demo.CONTRACTED == terms.App(terms.Lam(terms.Var(0)), terms.Var(0))
    assert demo.SIZE_OF_THREE == 9
    assert demo.church(2) == terms.Lam(
        terms.Lam(terms.App(terms.Var(1), terms.App(terms.Var(1), terms.Var(0))))
    )
    assert demo.TWO_STEPS == reduction.BetaStep(
        reduction.App1(reduction.Beta()),
        reduction.BetaStep(reduction.Beta(), reduction.BetaRefl()),
    )
    assert demo.JOINED == demo.LIFTED_CONTEXT


def test_the_demo_runs_as_a_script():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [EDITIONS, os.path.dirname(os.path.dirname(os.path.dirname(EDITIONS)))]
    )
    result = subprocess.run(
        [sys.executable, os.path.join(PACKAGE, "demo.py")],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60.0,
    )
    assert result.returncode == 0, result.stderr
    assert "SIZE_OF_THREE = 9" in result.stdout
    assert "THREE = Lam(body=Lam(body=App(" in result.stdout


def test_the_command_line_checks_the_edition():
    environment = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-m", "pcc.proof", "check", PACKAGE],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120.0,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(EDITIONS))),
    )
    assert result.returncode == 0, result.stderr
    assert "church_turing.lambda_untyped: 14 declarations" in result.stdout


def _tampered(tmp_path, module, old, new):
    """A copy of the edition with one edit to ``module``; returns its path."""
    import shutil

    copy = tmp_path / "church_turing"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / f"{module}.py"
    text = target.read_text(encoding="utf-8")
    assert old in text
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def _check_rejects(path, message):
    from pcc.proof.session import CheckError

    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous, 20000))
    try:
        with pytest.raises(CheckError, match=message):
            Session().check_file(str(path))
    finally:
        sys.setrecursionlimit(previous)


def test_determinism_needs_both_cases(tmp_path):
    path = _tampered(
        tmp_path,
        "lambda_weak_head",
        """        case (WhApp(s1), WhApp(s2)):
            return cong_app_function(wh_deterministic(s1, s2))
""",
        "",
    )
    _check_rejects(path, "does not cover the case WhApp")


def test_a_redex_is_not_in_weak_head_normal_form(tmp_path):
    path = _tampered(
        tmp_path,
        "lambda_weak_head",
        "def whnf_lam(b: Term) -> IsWhnf(Lam(b)):",
        "def whnf_lam(b: Term) -> IsWhnf(App(Lam(b), b)):",
    )
    _check_rejects(path, "is required")


def test_a_wrong_reduct_is_rejected(tmp_path):
    path = _tampered(
        tmp_path,
        "demo",
        "ONE_STEP: Step[App(IDENTITY, Var(7)), Var(7)] = Beta()",
        "ONE_STEP: Step[App(IDENTITY, Var(7)), Var(8)] = Beta()",
    )
    _check_rejects(path, "is required")


def test_a_bound_cannot_be_lowered(tmp_path):
    path = _tampered(
        tmp_path,
        "lambda_weak_head",
        "def whn_in_weaken(n: Nat, m: Term, h: WHNIn(n, m)) -> WHNIn(suc(n), m):",
        "def whn_in_weaken(n: Nat, m: Term, h: WHNIn(suc(n), m)) -> WHNIn(n, m):",
    )
    _check_rejects(path, "is required|cannot match|does not cover")

"""CI runs the one interpreter .python-version names, on every platform.

Workflows used to pick interpreters per job (``--python 3.13``,
``UV_PYTHON``, ``uv_python`` matrix entries, ``python-version: "3.13"``),
so moving the host meant editing a dozen places and Linux/Windows stayed on
3.13 while macOS ran 3.15. uv reads ``.python-version`` itself and
setup-python reads it through ``python-version-file``. The Docker wheel
checks are the one deliberate exception: they install on the package's
requires-python floor.

An explicit pip installation destination is not a second interpreter selection
when the same bash step resolves it and fails unless it equals setup-python.
Only that checked destination is exempted below, not other Python selectors.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


def _floor() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    match = re.fullmatch(r">=\s*(\d+\.\d+)", project["requires-python"])
    assert match, project["requires-python"]
    return match.group(1)


def _without_checked_install_targets(text: str) -> str:
    """Recognize one straight-line recipe, not arbitrary shell control flow.

    Keep this aligned with the locked dependency recipe checked separately by
    test_ci_pcc_ab_gate. Here names may vary, but both resolved paths, the
    fail-fast equality guard and the install must remain in the same bash step.
    Anything outside the complete recipe retains its selector and fails below.
    """
    resolve = "from pathlib import Path; import sys; print(Path(sys.executable).resolve())"

    def checked_step(match):
        step = match.group()
        lines = step.rstrip("\n").splitlines()
        if len(lines) != 10:
            return step
        assignments = [re.match(r"          ([A-Z_][A-Z0-9_]*)=", lines[i]) for i in (4, 5, 7)]
        if not all(assignments):
            return step
        setup, target, requirements = (item.group(1) for item in assignments)
        if len({setup, target, requirements}) != 3:
            return step
        path = re.fullmatch(
            rf'          {requirements}="(\$RUNNER_TEMP/[A-Za-z0-9_.-]+'
            r'-\$GITHUB_RUN_ID-\$GITHUB_RUN_ATTEMPT\.txt)"', lines[7],
        )
        if not path:
            return step
        expected = f'''        shell: bash
        run: |
          set -euo pipefail
          {setup}="$(python -I -c '{resolve}')"
          {target}="$(uv run --frozen --no-sync python -I -c '{resolve}')"
          test "${target}" = "${setup}"
          {requirements}="{path.group(1)}"
          uv export --frozen --only-group dev --no-emit-project --format requirements.txt --output-file "${requirements}"
          uv pip install --python "${target}" --require-hashes --only-binary :all: --reinstall --no-deps --no-python-downloads --requirements "${requirements}"'''
        if lines[1:] != expected.splitlines():
            return step
        # A setup action in a different job, or after the install, cannot bind
        # this step's plain `python` to the checked-in version.
        # Reset at every two-space nonblank header, including quoted job keys
        # and keys followed by comments. Unfamiliar headers must fail closed.
        job_prefix = re.split(r"(?m)^  \S[^\n]*(?:\n|$)", text[:match.start()])[-1]
        if not re.search(
            r"(?m)^      - uses: actions/setup-python@[^\n]+\n"
            r"        with:\n          python-version-file: \.python-version\n"
            r"(?=      - |\Z)",
            job_prefix,
        ):
            return step
        return step.replace(f'uv pip install --python "${target}"', "uv pip install", 1)

    return re.sub(
        r"(?m)^      - name: [^\n]+\n(?:(?:        [^\n]*|[ \t]*)\n)*",
        checked_step,
        text,
    )


def _require_python_version_source(text: str, name: str = "workflow") -> None:
    selectors = _without_checked_install_targets(text)
    for override in ("UV_PYTHON:", "uv_python", "python-version:"):
        assert override not in selectors, (name, override)
    # Include equals/tab spelling, not only '--python '. A short selector
    # immediately after uv's subcommand is also an override; do not mistake
    # downstream arguments such as `uv run pytest -p plugin` for uv options.
    assert not re.search(r"--python(?:\s|=)", selectors), name
    assert not re.search(r"\buv[ \t]+(?:run|pip[ \t]+install)[ \t]+-p", selectors), name
    assert not re.search(r"\bUV_PYTHON\s*=", selectors), name
    for step in text.split("uses: actions/setup-python@")[1:]:
        assert "python-version-file: .python-version" in step.split("\n      - ", 1)[0]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_takes_its_interpreter_from_python_version(workflow):
    _require_python_version_source(workflow.read_text(encoding="utf-8"), workflow.name)


CHECKED_INSTALL = '''jobs:
  dependencies:
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version-file: .python-version
      - name: Install locked dependencies
        shell: bash
        run: |
          set -euo pipefail
          SETUP_PYTHON="$(python -I -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
          CAMPAIGN_PYTHON="$(uv run --frozen --no-sync python -I -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
          test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"
          REQUIREMENTS="$RUNNER_TEMP/locked-dev-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.txt"
          uv export --frozen --only-group dev --no-emit-project --format requirements.txt --output-file "$REQUIREMENTS"
          uv pip install --python "$CAMPAIGN_PYTHON" --require-hashes --only-binary :all: --reinstall --no-deps --no-python-downloads --requirements "$REQUIREMENTS"
'''


@pytest.mark.parametrize("renamed", [False, True])
def test_install_destination_accepts_only_the_checked_interpreter(renamed):
    text = CHECKED_INSTALL
    if renamed:
        for before, after in (
            ("SETUP_PYTHON", "BASE_EXECUTABLE"), ("CAMPAIGN_PYTHON", "INSTALL_EXECUTABLE"),
            ("REQUIREMENTS", "LOCKED_WHEELS"), ("locked-dev", "requirements"),
            ("dependencies:", "other-job:"), ("Install locked dependencies", "Install wheels"),
        ):
            text = text.replace(before, after)
    _require_python_version_source(text)
    assert _without_checked_install_targets(text) == text.replace(
        '--python "$INSTALL_EXECUTABLE" ' if renamed else '--python "$CAMPAIGN_PYTHON" ', "", 1,
    )


@pytest.mark.parametrize("before,after", [
    ('--python "$CAMPAIGN_PYTHON"', "--python 3.13"),
    ('--python "$CAMPAIGN_PYTHON"', "--python=3.13"),
    ('--python "$CAMPAIGN_PYTHON"', '--python "$OTHER_PYTHON"'),
    ('--python "$CAMPAIGN_PYTHON"', '--python "$SETUP_PYTHON"'),
    ('--python "$CAMPAIGN_PYTHON"', '--python\t3.13'),
    ('--python "$CAMPAIGN_PYTHON"', '-p 3.13'),
    ('--python "$CAMPAIGN_PYTHON"', '-p3.13'),
    ('--python "$CAMPAIGN_PYTHON"', '--python "$CAMPAIGN_PYTHON" --python=3.13'),
    ('          test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"\n', ""),
    ('test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"', 'test "$CAMPAIGN_PYTHON" = "$CAMPAIGN_PYTHON"'),
    ('test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"', 'test "$CAMPAIGN_PYTHON" != "$SETUP_PYTHON"'),
    ('test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"', 'test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON" || true'),
    ("set -euo pipefail", "set +e"),
    ("shell: bash", "shell: sh"),
    ("$(python -I", "$(python3.13 -I"),
    ("uv run --frozen --no-sync python", "uv run --python 3.13 python"),
    ("Path(sys.executable).resolve()", "Path(sys.executable)"),
    ("CAMPAIGN_PYTHON", "SETUP_PYTHON"),
    ("REQUIREMENTS", "CAMPAIGN_PYTHON"),
    ("          uv pip install", '          CAMPAIGN_PYTHON=/usr/bin/python3.13\n          uv pip install'),
    ("          uv pip install", '          if false; then\n          uv pip install'),
    ("          uv pip install", "      - name: Install in another step\n        run: |\n          uv pip install"),
    ('--output-file "$REQUIREMENTS"', '--output-file "$REQUIREMENTS"; CAMPAIGN_PYTHON=/usr/bin/python3.13'),
    ("--no-python-downloads", ""),
    ("python-version-file: .python-version", 'python-version: "3.13"'),
    ("python-version-file: .python-version", "python-version-file: .python-version\n        if: false"),
    ("      - name: Install locked dependencies", "  other-job:\n    steps:\n      - name: Install locked dependencies"),
    ("      - name: Install locked dependencies", "  other-job: # comment\n    steps:\n      - name: Install locked dependencies"),
    ("      - name: Install locked dependencies", '  "other-job":\n    steps:\n      - name: Install locked dependencies'),
])
def test_install_destination_rejects_unverified_or_divergent_interpreters(before, after):
    assert before in CHECKED_INSTALL
    with pytest.raises(AssertionError):
        _require_python_version_source(CHECKED_INSTALL.replace(before, after))


def test_install_destination_rejects_a_guard_after_install():
    guard = '          test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"\n'
    with pytest.raises(AssertionError):
        _require_python_version_source(CHECKED_INSTALL.replace(guard, "") + guard)


@pytest.mark.parametrize("mutation", ["duplicate", "suffix"])
def test_install_destination_rejects_extra_commands(mutation):
    command = next(line for line in CHECKED_INSTALL.splitlines() if line.startswith("          uv pip install"))
    replacement = command + "\n" + command if mutation == "duplicate" else command + "; true"
    with pytest.raises(AssertionError):
        _require_python_version_source(CHECKED_INSTALL.replace(command, replacement))


@pytest.mark.parametrize("command", [
    "uv run --python 3.13 python", "uv run --python=3.13 python", "uv run -p 3.13 python",
    'uv pip install --python "$CAMPAIGN_PYTHON" pytest',
    "UV_PYTHON: 3.13", "UV_PYTHON=3.13 uv run python", "uv_python: 3.13", 'python-version: "3.13"',
])
def test_checked_install_does_not_exempt_other_selectors(command):
    with pytest.raises(AssertionError):
        _require_python_version_source(CHECKED_INSTALL + f"      - run: {command}\n")


@pytest.mark.parametrize("command", [
    "uv run pytest -p plugin", "uv run pytest -pno:cacheprovider",
    "uv run python script.py -p value",
])
def test_downstream_application_options_are_not_uv_interpreter_selectors(command):
    _require_python_version_source(CHECKED_INSTALL + f"      - run: {command}\n")


def test_only_the_docker_wheel_checks_name_a_python_and_it_is_the_floor():
    floor = _floor()
    for workflow in WORKFLOWS:
        for image in re.findall(r"\bpython:(\d+\.\d+)-", workflow.read_text(encoding="utf-8")):
            assert image == floor, (workflow.name, image)


def test_gate_reads_the_exact_release_from_python_version():
    sys.path.insert(0, str(ROOT))
    try:
        from scripts.ci_worker_size_prior_gate import python_version_pin
    finally:
        sys.path.remove(str(ROOT))
    assert python_version_pin("3.15.0\n") == (3, 15, 0, "final", 0)
    assert python_version_pin("3.15.0rc1") == (3, 15, 0, "candidate", 1)
    assert python_version_pin("3.14.0b2") == (3, 14, 0, "beta", 2)
    with pytest.raises(ValueError):
        python_version_pin("3.15")
    pinned = python_version_pin((ROOT / ".python-version").read_text(encoding="utf-8"))
    assert pinned[:2] >= tuple(int(part) for part in _floor().split("."))

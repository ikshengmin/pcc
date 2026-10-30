"""Finite contracts for existing ordinary Stage2/3 artifact transport only."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pcc1-package-parity.yml"
STEP_NAMES = {
    "nonmac": "Preserve platform qualification evidence",
    "macos": "Preserve macOS bootstrap evidence",
}
BASE_PATHS = {'nonmac': ['build/platform-bootstrap/**/receipt.json', 'build/platform-bootstrap/**/failure.json', 'build/platform-bootstrap/**/progress.json', 'build/platform-qualification/**/*.log', 'build/platform-qualification/**/*.json', 'build/platform-qualification/**/*.jsonl', 'build/platform-qualification/gc0/stage2-export-evidence/selected.manifest', 'build/platform-qualification/gc0/stage2-export-evidence/result.tsv', 'build/worker-prior-gate/**/*.json', 'build/worker-prior-gate/**/*.jsonl', 'build/worker-prior-gate/**/*.log', 'build/worker-prior-gate/**/*.stdout', 'build/worker-prior-gate/**/*.stderr', 'build/worker-prior-gate/**/*.tsv', 'build/worker-prior-gate/**/*.xml'], 'macos': ['build/bootstrap-pytest-self*/profile/stage*.result.json', 'build/bootstrap-pytest-self*/profile/stage*.process.*/result.json', 'build/bootstrap/stage*.process.*/result.json', 'build/bootstrap/stage*.process.*/samples.tsv', 'build/bootstrap/stage*.process.*/target.stdout', 'build/bootstrap/stage*.process.*/target.stderr', 'build/bootstrap/stage*.process.*/result.json.worker-rss.tsv', 'build/bootstrap/stage*.process.*/result.json.process-rss.jsonl', 'build/bootstrap/stage*.json', 'build/worker-prior-gate/**/*.json', 'build/worker-prior-gate/**/*.jsonl', 'build/worker-prior-gate/**/*.log', 'build/worker-prior-gate/**/*.stdout', 'build/worker-prior-gate/**/*.stderr', 'build/worker-prior-gate/**/*.tsv', 'build/worker-prior-gate/**/*.xml']}


def _tree(path):
    return ast.parse((ROOT / path).read_text())


def _function(tree, name):
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(functions) == 1
    return functions[0]


def _nonmac_paths():
    # Evaluate only the real path expression, never run_chain or its compiler.
    chain = _function(_tree("scripts/bootstrap_platform.py"), "run_chain")
    expressions = [keyword.value for node in ast.walk(chain)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "guarded" for keyword in node.keywords
                   if keyword.arg == "log_path"
                   and isinstance(keyword.value, ast.BinOp)
                   and isinstance(keyword.value.right, ast.JoinedStr)
                   and ast.unparse(keyword.value.right) == "f'stage{stage}.log'"]
    assert len(expressions) == 1
    expression = compile(ast.Expression(expressions[0]), "<actual stage log path>", "eval")
    watchdog = _function(_tree("scripts/platform_process_watchdog.py"), "run")
    suffixes = {node.args[0].value: node for node in ast.walk(watchdog)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "log_path"
                and node.func.attr == "with_suffix" and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)}
    assert set(suffixes) == {".resources.jsonl", ".windows-job.json"}
    paths = []
    for gc in range(5):
        for stage in (2, 3):
            log = eval(expression, {"__builtins__": {}},
                       {"out_dir": Path("build/platform-bootstrap") / ("gc" + str(gc)), "stage": stage})
            paths.append(log.as_posix())
            for suffix in (".resources.jsonl", ".windows-job.json"):
                path = eval(compile(ast.Expression(suffixes[suffix]), "<actual stage resource path>", "eval"),
                            {"__builtins__": {}}, {"log_path": log})
                paths.append(path.as_posix())
    return paths


def _macos_paths():
    # These two authentic functions only construct paths/argv. Do not import
    # the original native-test module or invoke the constructed command.
    tree = _tree("tests/python/test_pcc_bootstrap_full.py")
    functions = [_function(tree, name) for name in
                 ("_bootstrap_out_dir", "_bootstrap_stage_sampler_command")]
    namespace = {"Path": Path, "sys": SimpleNamespace(executable="finite-host-python"),
                 "_REPO_ROOT": Path("."), "_PROCESS_TREE_SAMPLER": Path("scripts/run_process_tree_sample.py"),
                 "_BOOTSTRAP_STAGE_TIMEOUT_S": 600,
                 "_BOOTSTRAP_MAX_TREE_RSS_BYTES": 8 * 1024 * 1024 * 1024}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                             *deepcopy(functions)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<actual path-only helpers>", "exec"), namespace)
    paths = []
    for gc in range(5):
        profile = namespace["_bootstrap_out_dir"](str(gc)) / "profile"
        for stage in (2, 3):
            argv, stdout, stderr = namespace["_bootstrap_stage_sampler_command"](
                ["never-executed"], profile_dir=profile, stage=stage)
            samples = Path(argv[argv.index("--samples") + 1])
            paths.extend(path.as_posix() for path in (stdout, stderr, samples))
    return paths


def _step_paths(kind):
    workflow = WORKFLOW.read_text()
    marker = "      - name: " + STEP_NAMES[kind] + "\n"
    assert workflow.count(marker) == 1
    block = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    assert "        if: always()\n" in block
    assert "        uses: actions/upload-artifact@v6\n" in block
    lines = block.split("          path: |\n", 1)[1].splitlines()
    return [line.strip() for line in lines if line.startswith("            ") and line.strip()]


def _assert_transport(kind, paths, expected):
    assert len(paths) == len(set(paths))
    assert [path for path in paths if path in BASE_PATHS[kind]] == BASE_PATHS[kind]
    added = [path for path in paths if path not in BASE_PATHS[kind]]
    assert added == expected
    assert all(not any(character in path for character in "*?[]") for path in added)
    assert all(not path.startswith("/") and ".." not in Path(path).parts for path in added)


@pytest.mark.parametrize("kind", ["nonmac", "macos"])
def test_existing_coverage_and_exact_actual_stage_output_paths(kind):
    expected = _nonmac_paths() if kind == "nonmac" else _macos_paths()
    assert len(expected) == 30
    _assert_transport(kind, _step_paths(kind), expected)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "stage1", "canary", "native-prior", "binary"])
def test_missing_or_unrelated_transport_is_rejected(mutation):
    expected = _nonmac_paths()
    paths = _step_paths("nonmac")
    if mutation == "missing":
        paths.remove(expected[0])
    elif mutation == "duplicate":
        paths.append(expected[0])
    else:
        paths.append({"stage1": "build/platform-bootstrap/gc0/stage1.log",
                      "canary": "build/platform-bootstrap/gc0/canary2-compile.log",
                      "native-prior": "build/worker-prior-gate/native/target.stdout",
                      "binary": "build/platform-bootstrap/gc0/pcc2"}[mutation])
    with pytest.raises(AssertionError):
        _assert_transport("nonmac", paths, expected)

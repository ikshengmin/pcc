#!/usr/bin/env python3
"""Explicit candidate-only assertions under the CI campaign's existing guard.

This is a pytest adapter, not a compiler/benchmark owner. It registers the
unchanged shared compiler fixture, supplies only receipt-bound artifacts, and
records the unchanged assertion programs' real process results. Ordinary
source gates retain their original six workers and 90s/1-GiB limits. This
separate six-worker, single-evidence-group phase has a 900s ceiling inside the same absolute campaign
deadline and sampled tree budget, with no new sampler or resource admission.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import os
import signal
from pathlib import Path
import sys
import time
import types

import pytest

SCHEMA = "pcc.candidate-import-scan-mechanism.v1"
SOURCE = "pcc/frontends/python/pipeline_import_scan.py"
HELPER = "_without_attribute_error_handler_imports"
FIXTURE = "tests/python/conftest.py"
ARTIFACTS = {
    "identity_cases.py": "e0c038728fa336716565c5a7daaf91f1646fbe8d8db0d5732fd4502b11a2420a",
    "native_cases.py": "3733fece119c39b9f6410c4d8a504d92c47c659fc64d523740f4246f3f9870fc",
}
DATA = "scripts/data/ci_ab_import_scan/"
PHASE_SECONDS = 900
EXPECTED = "attribute-error-import-filter-ok\n"
UNIT_NAMES = (
    "test_attribute_error_filter_reuses_text_without_handler_marker",
    "test_attribute_error_prefilter_keeps_all_splitlines_separators",
)
REFERENCE = "test_attribute_error_filter_cpython_reference"
NATIVE = "test_attribute_error_filter_native"
GROUP = "candidate_import_scan_mechanism"
LANES = {"ordinary": UNIT_NAMES, "reference": (REFERENCE,), "integration": (NATIVE,)}
CONFTEST = (
    "from pcc_ci_ab_mechanism import install_fixtures, configure\n"
    "install_fixtures(globals())\n"
    "def pytest_configure(config):\n"
    "    configure(config)\n"
)
PUBLIC_ENV_KEYS = (
    "PATH", "HOME", "TMPDIR", "XDG_CACHE_HOME", "LANG", "PYTHONHASHSEED", "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_ADDOPTS",
    "PCC_GC_BACKEND", "PCC_RUNTIME_CC", "PCC_RUNTIME_HIGH", "PCC_SELF_LINK",
    "PCC_SELF_BACKEND_PUBLISH_SYNC", "PCC_PYTHON_IR_PASSES", "PCC_PYTHON_IR_PASS_JOBS",
    "PCC_PY_FRONTEND_IR_CACHE", "PCC_SELF_BACKEND_OBJECT_CACHE", "PCC_PY_FRONTEND_JOBS",
    "PCC_SELF_BACKEND_JOBS", "PCC_MACHO_LINK_JOBS", "PCC_DEBUG_IR_CALL", "PCC_DEBUG_IR_RENDER",
    "PCC_RUNTIME_ARCHIVE", "PCC_SOURCE_ROOT", "PCC_REPO_ROOT", "PCC_HOST_PYTHON", "PCC_HOST_PCC",
    "PCC_NO_AUTO_PCC1", "PCC_CURRENT_PCC1", "PCC_REQUIRE_PCC1", "PCC_DEBUG_WORKER_RERAISE",
    "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_TREE_STATE_PATH", "PCC_WORKER_TREE_RECOVERY",
    "DEVELOPER_DIR", "SDKROOT", "MACOSX_DEPLOYMENT_TARGET",
)
_STATE = None


def public_environment(environment):
    return {key: environment[key] for key in PUBLIC_ENV_KEYS if key in environment}


def _owned(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise MechanismError("unsafe mechanism artifact path")
    result = root / path
    if result.is_symlink() or result.resolve(strict=True) != result or not result.is_file():
        raise MechanismError("unsafe mechanism artifact")
    return result


class MechanismError(RuntimeError):
    pass


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MechanismError("missing mechanism dependency: " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def program_bytes(source, artifact, *, native):
    """Extract exact source slices/constants, never rewrite the helper/model."""
    text = source.read_text(encoding="utf-8")
    nodes = [node for node in ast.parse(text).body
             if isinstance(node, ast.FunctionDef) and node.name == HELPER]
    if len(nodes) != 1 or nodes[0].decorator_list:
        raise MechanismError("unsupported production helper shape")
    node = nodes[0]
    helper = "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
    constants = {}
    for entry in ast.parse(artifact.read_text(encoding="utf-8")).body:
        if isinstance(entry, ast.Assign) and len(entry.targets) == 1:
            target = entry.targets[0]
            if isinstance(target, ast.Name) and target.id in {"PROGRAM", "GC_REPORT", "EXPECTED"}:
                constants[target.id] = ast.literal_eval(entry.value)
    if set(constants) != {"PROGRAM", "GC_REPORT", "EXPECTED"} or constants["EXPECTED"] != EXPECTED:
        raise MechanismError("preserved native assertion artifact shape changed")
    return (helper + "\n" + constants["PROGRAM"]
            + (constants["GC_REPORT"] if native else "")).encode("utf-8")


def expected_cases(artifact):
    cases = []
    for node in ast.parse(artifact.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.FunctionDef) or node.name not in UNIT_NAMES:
            continue
        if len(node.decorator_list) != 1:
            raise MechanismError("identity assertion parametrization changed")
        mark = node.decorator_list[0]
        if not isinstance(mark, ast.Call) or len(mark.args) != 2:
            raise MechanismError("identity assertion parametrization changed")
        argument, values = (ast.literal_eval(arg) for arg in mark.args)
        cases.extend({"name": node.name, "parameters": {argument: value}} for value in values)
    if len(cases) != 16 or len({json.dumps(row, sort_keys=True) for row in cases}) != 16:
        raise MechanismError("expected exactly sixteen distinct identity assertions")
    return cases + [{"name": REFERENCE, "parameters": {}}] + [
        {"name": NATIVE, "parameters": {"python_program_compiler": owner}}
        for owner in ("pcc0", "pcc1")
    ]


def validate_result(result, *, request, root):
    """Parent independently checks executions, not a success word or exit code."""
    if (result.get("schema") != SCHEMA or result.get("status") != "PASSED"
            or result.get("request_sha256") != _sha(request)
            or result.get("pytest_exitcode") != 0 or result.get("assertions_enabled") is not True
            or result.get("implementation") != "cpython"):
        raise MechanismError("mechanism result is not a complete assertion run")
    payload = json.loads(request.read_text())
    expected = expected_cases(root / "inputs/identity_cases.py")
    collected = result.get("collected", [])
    identities = [{"name": row.get("name"), "parameters": row.get("parameters")} for row in collected]
    key = lambda value: json.dumps(value, sort_keys=True)
    if sorted(map(key, identities)) != sorted(map(key, expected)):
        raise MechanismError("mechanism collection is incomplete or unexpected")
    nodes = {row["nodeid"] for row in collected}
    if len(nodes) != 19:
        raise MechanismError("mechanism node identities are not unique")
    reports = result.get("reports", [])
    expected_reports = {(node, when) for node in nodes for when in ("setup", "call", "teardown")}
    actual = {(row.get("nodeid"), row.get("when")) for row in reports}
    if (len(reports) != 57 or actual != expected_reports
            or any(row.get("outcome") != "passed" or row.get("wasxfail") is not None for row in reports)):
        raise MechanismError("mechanism requires all setup/call/teardown reports; no skip or xfail")
    executions = result.get("executions", [])
    if len(executions) != 11:
        raise MechanismError("mechanism requires CPython plus both owners on five collectors")
    expected_runs = {("cpython", None)} | {(owner, gc) for owner in ("pcc0", "pcc1") for gc in range(5)}
    actual_runs = {(row.get("owner"), row.get("gc")) for row in executions}
    if actual_runs != expected_runs:
        raise MechanismError("mechanism owner/collector execution is incomplete")
    for row in executions:
        expected_output = EXPECTED + ("" if row["owner"] == "cpython" else "GC " + str(row["gc"]) + "\n")
        if (row.get("returncode"), row.get("stdout"), row.get("stderr")) != (0, expected_output, ""):
            raise MechanismError("mechanism output differs from the real reference/GC observer")
        if row["owner"] != "cpython" and (
                row.get("environment", {}).get("PCC_GC_BACKEND") != str(row["gc"])
                or row.get("environment", {}).get("PATH") != "/nonexistent"):
            raise MechanismError("mechanism did not preserve native no-fallback collector selection")
        if set(row.get("environment", {})) - set(PUBLIC_ENV_KEYS):
            raise MechanismError("mechanism evidence contains unexpected environment settings")
        for key_name in ("stdout", "stderr"):
            if _sha(_owned(root, row[key_name + "_path"])) != hashlib.sha256(row[key_name].encode()).hexdigest():
                raise MechanismError("mechanism output transcript changed")
    compiles = result.get("compiles", [])
    if len(compiles) != 2 or {row.get("owner") for row in compiles} != {"pcc0", "pcc1"}:
        raise MechanismError("mechanism must compile through both actual fixture owners")
    for row in compiles:
        linkage = row.get("linkage", {})
        if (linkage.get("checked") is not True or linkage.get("links_libpython") is not False
                or linkage.get("links_llvm") is not False):
            raise MechanismError("mechanism emitted output has no strict native linkage proof")
        binary = _owned(root, row["binary"])
        if _sha(binary) != row.get("binary_sha256"):
            raise MechanismError("mechanism native binary changed after use")
        if row.get("fixture_sha256") != payload["source_files"].get(FIXTURE, {}).get("sha256"):
            raise MechanismError("mechanism used a different shared compiler fixture")
        if row["owner"] == "pcc1":
            native_compile = row.get("native_compile") or {}
            command = native_compile.get("command", [])
            if (len(command) != 8 or command[:5] != [payload["compiler"], "--backend", "self", "--ir-scaffold", "on"]
                    or command[6:] != ["-o", str(binary)] or native_compile.get("returncode") != 0
                    or native_compile.get("environment", {}).get("PCC_RUNTIME_ARCHIVE") != payload["runtime_archive"]
                    or native_compile.get("environment", {}).get("PCC_HOST_PYTHON") != "/usr/bin/false"
                    or native_compile.get("environment", {}).get("PCC_HOST_PCC") != "/usr/bin/false"):
                raise MechanismError("mechanism lacks the actual strict candidate native compiler invocation")
        else:
            host_origin = row.get("host_origin") or {}
            path = Path(host_origin.get("path", ""))
            try:
                relative = path.relative_to(Path(payload["source_root"])).as_posix()
            except ValueError as exc:
                raise MechanismError("mechanism host compiler import escaped candidate") from exc
            expected_origin = payload["source_files"].get(relative, {})
            if (not relative.startswith("pcc/") or _sha(path) != expected_origin.get("sha256")
                    or host_origin.get("sha256") != expected_origin.get("sha256")
                    or host_origin.get("name") != "compile_python"):
                raise MechanismError("mechanism host compiler origin is not qualified")
        matching = [run for run in executions if run["owner"] == row["owner"]]
        if any(run.get("binary_sha256") != row["binary_sha256"]
               or run.get("command") != [str(binary)] for run in matching):
            raise MechanismError("mechanism executed a different native output")
    programs = result.get("programs", [])
    if len(programs) != 3 or {row.get("owner") for row in programs} != {"cpython", "pcc0", "pcc1"}:
        raise MechanismError("mechanism generated program inventory is incomplete")
    payload = json.loads(request.read_text())
    for row in programs:
        program = _owned(root, row["path"])
        expected_program = program_bytes(Path(payload["source_root"]) / SOURCE,
                                         root / "inputs/native_cases.py", native=row["owner"] != "cpython")
        if program.read_bytes() != expected_program or _sha(program) != row.get("sha256"):
            raise MechanismError("mechanism program is not the actual qualified helper and full assertions")
        if row["owner"] == "cpython":
            reference = next(run for run in executions if run["owner"] == "cpython")
            host_file = result.get("host_python_runtime", {}).get("runtime_files", {}).get("executable", {})
            if (reference.get("command") != [host_file.get("path"), str(program)]
                    or reference.get("binary_sha256") != host_file.get("sha256")):
                raise MechanismError("mechanism reference did not execute receipt-bound CPython")
        else:
            compiled = next(entry for entry in compiles if entry["owner"] == row["owner"])
            if row["owner"] == "pcc1" and compiled["native_compile"]["command"][5] != str(program):
                raise MechanismError("mechanism native compiler consumed another program")
    origins = result.get("import_origins", {})
    if "pcc.frontends.python.pipeline_import_scan" not in origins:
        raise MechanismError("mechanism lacks actual production helper import")
    for name, row in origins.items():
        path = Path(row["path"])
        try:
            relative = path.relative_to(Path(payload["source_root"])).as_posix()
        except ValueError as exc:
            raise MechanismError("mechanism import origin escaped candidate") from exc
        expected = payload["source_files"].get(relative)
        if expected is None or _sha(path) != expected["sha256"] or row.get("sha256") != expected["sha256"]:
            raise MechanismError("mechanism import origin changed after use")
    if not origins or not result.get("host_python_runtime"):
        raise MechanismError("mechanism lacks bound Python runtime/import origins")

    lanes = result.get("lanes", {})
    if set(lanes) != set(LANES):
        raise MechanismError("mechanism requires separate ordinary/reference/integration invocations")
    aggregated = {key: [] for key in ("collected", "reports", "executions", "compiles", "programs")}
    for lane, lane_receipt in lanes.items():
        lane_path = _owned(root, lane_receipt["path"])
        if _sha(lane_path) != lane_receipt["sha256"]:
            raise MechanismError("mechanism lane result changed")
        invocation = json.loads(lane_path.read_text())
        validate_worker_evidence(invocation, lane=lane, payload=payload, root=root, aggregate=result)
        for key in aggregated:
            aggregated[key].extend(invocation[key])
    order = lambda row: json.dumps(row, sort_keys=True)
    for key, rows in aggregated.items():
        if sorted(map(order, rows)) != sorted(map(order, result[key])):
            raise MechanismError("mechanism lane evidence is not the exact disjoint aggregate")


def validate_worker_evidence(invocation, *, lane, payload, root, aggregate):
    xdist = invocation.get("xdist", {})
    worker_rows = xdist.get("worker_results", {})
    if (invocation.get("lane") != lane or invocation.get("status") != "PASSED"
            or invocation.get("pytest_exitcode") != 0
            or xdist.get("workers") != 6 or xdist.get("dist") != "loadgroup" or xdist.get("group") != GROUP
            or set(worker_rows) != {"gw" + str(n) for n in range(6)}
            or xdist.get("executing_worker") not in worker_rows):
        raise MechanismError("mechanism lacks actual six-worker lane execution evidence")
    controller_receipt = invocation.get("controller", {})
    controller_path = _owned(root, controller_receipt.get("path", ""))
    if _sha(controller_path) != controller_receipt.get("sha256"):
        raise MechanismError("mechanism controller evidence changed")
    controller = json.loads(controller_path.read_text())
    if (controller.get("reports") != invocation.get("reports") or controller.get("pytest_exitcode") != 0
            or controller.get("completed_workers") != worker_rows or controller.get("lane") != lane
            or controller.get("request_sha256") != aggregate["request_sha256"]
            or controller.get("worker_identity", {}).get("worker") is not None):
        raise MechanismError("mechanism actual controller and worker reports disagree")
    expected_selection = [str(root / "inputs/identity_cases.py")] if lane == "ordinary" else [
        str(root / "inputs/native_cases.py") + "::" + (REFERENCE if lane == "reference" else NATIVE)]
    if invocation.get("selection") != expected_selection:
        raise MechanismError("mechanism reference/integration selection was not separate")
    expected = [row for row in expected_cases(root / "inputs/identity_cases.py") if row["name"] in LANES[lane]]
    collected = invocation.get("collected", [])
    identities = [{"name": row.get("name"), "parameters": row.get("parameters")} for row in collected]
    key = lambda row: json.dumps(row, sort_keys=True)
    if (sorted(map(key, expected)) != sorted(map(key, identities))
            or any(not row["nodeid"].endswith("@" + GROUP) for row in collected)):
        raise MechanismError("mechanism lane collection selected unexpected cases")
    pids = set()
    for name, row in worker_rows.items():
        path = _owned(root, row["path"])
        if _sha(path) != row["sha256"] or row.get("worker") != name:
            raise MechanismError("mechanism worker evidence changed")
        worker = json.loads(path.read_text())
        identity = worker.get("worker_identity", {})
        if (identity.get("worker") != name or identity.get("workercount") != 6 or identity.get("lane") != lane
                or identity.get("request_sha256") != aggregate["request_sha256"]
                or type(identity.get("pid")) is not int or identity["pid"] <= 0
                or worker.get("collected") != collected
                or worker.get("request_sha256") != aggregate["request_sha256"]
                or worker.get("source_sha256") != aggregate.get("source_sha256")
                or worker.get("compiler_receipt_sha256") != payload["receipt_sha256"]
                or worker.get("guard_contract_sha256") != aggregate.get("guard_contract_sha256")
                or worker.get("host_python_runtime") != aggregate["host_python_runtime"]
                or worker.get("assertions_enabled") is not True or worker.get("implementation") != "cpython"
                or "pcc.frontends.python.pipeline_import_scan" not in worker.get("import_origins", {})):
            raise MechanismError("mechanism worker identity, collection or source/receipt binding differs")
        pids.add(identity["pid"])
        for origin in worker["import_origins"].values():
            origin_path = Path(origin["path"])
            try:
                relative = origin_path.relative_to(Path(payload["source_root"])).as_posix()
            except ValueError as exc:
                raise MechanismError("worker helper origin escaped candidate") from exc
            if origin.get("sha256") != payload["source_files"].get(relative, {}).get("sha256") or _sha(origin_path) != origin["sha256"]:
                raise MechanismError("worker helper origin changed")
        if name == xdist["executing_worker"]:
            for field in ("reports", "executions", "compiles", "programs"):
                if worker.get(field) != invocation[field]:
                    raise MechanismError("mechanism worker and lane execution evidence disagree")
            if worker.get("status") != "PASSED" or worker.get("pytest_exitcode") != 0:
                raise MechanismError("executing mechanism worker did not pass")
        elif (worker.get("status") != "IDLE" or worker.get("pytest_exitcode") != 0
              or any(worker.get(field) for field in ("reports", "executions", "compiles", "programs"))):
            raise MechanismError("unexpected work outside the owned mechanism evidence group")
    if len(pids) != 6:
        raise MechanismError("mechanism did not start six distinct worker processes")


@pytest.fixture(scope="session")
def native_pcc1_compiler():
    _STATE.verify()
    return _STATE.compiler


@pytest.fixture(scope="session")
def pcc_runtime_archive():
    _STATE.verify()
    return _STATE.runtime


@pytest.fixture
def tmp_path(request):
    # Keep emitted programs and outputs as evidence, without pytest's mutable
    # "current" symlinks or automatic cleanup outside the campaign's ownership.
    path = _STATE.root / "test-work" / _STATE.lane / hashlib.sha256(request.node.nodeid.encode()).hexdigest()
    path.mkdir(parents=True, exist_ok=False)
    return path


class Recorder:
    def __init__(self, request, ab, owner):
        self.request = request
        self.payload = json.loads(request.read_text())
        self.ab, self.owner = ab, owner
        self.root = request.parent
        self.source = Path(self.payload["source_root"])
        self.compiler = Path(self.payload["compiler"])
        self.runtime = Path(self.payload["runtime_archive"])
        self.deadline = self.payload["phase_deadline_monotonic"]
        contract = dict(self.payload["guard"])
        contract["guard_result_path"] = Path(contract["guard_result_path"])
        contract["expected_guard_cwd"] = Path(contract["expected_guard_cwd"])
        self.budget = ab._validated_worker_budget(**contract)
        self.result = {"schema": SCHEMA, "status": "RUNNING", "request_sha256": _sha(request),
                       "assertions_enabled": sys.flags.optimize == 0,
                       "implementation": sys.implementation.name, "collected": [], "reports": [],
                       "executions": [], "compiles": [], "programs": [], "import_origins": {}}
        self.native_module = None
        self.active_owner = None
        self.worker = os.environ.get("PYTEST_XDIST_WORKER")
        self.lane = os.environ.get("PCC_CI_AB_MECHANISM_LANE")
        if self.lane not in LANES:
            raise MechanismError("explicit mechanism lane selection is required")
        self.workers = {}
        self.record_path = self.root / (("worker-" + self.lane + "-" + self.worker if self.worker else "controller-" + self.lane) + ".json")
        self.result.update(lane=self.lane, source_sha256=owner._identity(self.payload["source_files"]),
                           compiler_receipt_sha256=self.payload["receipt_sha256"],
                           guard_contract_sha256=self.budget.receipt["guard_result"]["contract_sha256"])

    def persist(self):
        _write(self.record_path, self.result)

    def remaining(self, ceiling):
        remaining = min(self.budget.remaining_timeout(ceiling), self.deadline - time.monotonic())
        if remaining <= 0:
            raise MechanismError("candidate mechanism phase deadline exhausted")
        return remaining

    def verify(self):
        self.remaining(PHASE_SECONDS)
        if _sha(self.request) != self.result["request_sha256"]:
            raise MechanismError("mechanism launch request changed")
        if self.owner._tree(self.source) != self.payload["source_files"]:
            raise MechanismError("mechanism candidate source changed")
        for name, receipt in self.payload["inputs"].items():
            self.owner._file(receipt)
        for name, receipt in self.payload["tools"].items():
            self.owner._file(receipt, readonly=False)
            relative = name.removeprefix("tool/")
            if receipt["sha256"] != self.payload["source_files"].get(relative, {}).get("sha256"):
                raise MechanismError("mechanism tool is not qualified candidate source")
        if _sha(self.compiler) != self.payload["compiler_sha256"]:
            raise MechanismError("mechanism compiler changed")
        loaded = self.ab._load_build_receipt(
            Path(self.payload["receipt"]), arm="candidate", compiler_sha256=_sha(self.compiler),
            runtime_sha256=_sha(self.runtime), compiler_size_bytes=self.compiler.stat().st_size,
            expected_gc_backend=0)
        if loaded["sha256"] != self.payload["receipt_sha256"]:
            raise MechanismError("mechanism strict compiler receipt changed")
        if loaded["source_manifest"]["files"] != {
                name: row["sha256"] for name, row in self.payload["source_files"].items()
                if name in loaded["source_manifest"]["files"]}:
            raise MechanismError("strict compiler source is not this candidate")
        if (sys.flags.optimize != 0 or not sys.dont_write_bytecode
                or sys.implementation.name != "cpython" or os.environ.get("PYTHONOPTIMIZE")):
            raise MechanismError("real CPython with enabled assertions and no bytecode writes is required")
        return loaded

    def origins(self):
        origins = {}
        for name, module in list(sys.modules.items()):
            if not (name in {"pcc", "tests"} or name.startswith(("pcc.", "tests."))):
                continue
            filename = getattr(module, "__file__", None)
            if filename:
                path = Path(filename)
                if path.is_symlink() or path.resolve() != path:
                    raise MechanismError("noncanonical mechanism import: " + name)
                try:
                    relative = path.relative_to(self.source).as_posix()
                except ValueError as exc:
                    raise MechanismError("foreign mechanism import: " + name) from exc
                expected = self.payload["source_files"].get(relative)
                if expected is None or _sha(path) != expected["sha256"]:
                    raise MechanismError("unqualified mechanism import: " + name)
                origins[name] = {"path": str(path), "sha256": expected["sha256"]}
            elif not getattr(module, "__path__", None):
                raise MechanismError("unbound mechanism package: " + name)
            for path in getattr(module, "__path__", ()):
                if Path(path) != self.source.joinpath(*name.split(".")):
                    raise MechanismError("mechanism import path escapes candidate: " + name)
        if "pcc.frontends.python.pipeline_import_scan" not in origins:
            raise MechanismError("actual candidate helper was not imported")
        self.result["import_origins"].update(origins)

    def program(self, source, owner):
        source = Path(source)
        if self.root not in source.parents or source.is_symlink():
            raise MechanismError("mechanism program is outside owned output")
        expected = program_bytes(self.source / SOURCE, self.root / "inputs/native_cases.py",
                                 native=owner != "cpython")
        if source.read_bytes() != expected:
            raise MechanismError("emitted test source differs from the exact candidate helper")
        source.chmod(0o444)
        row = {"owner": owner, "path": source.relative_to(self.root).as_posix(), "sha256": _sha(source)}
        self.result["programs"].append(row)
        return row

    def execute(self, command, *, cwd, capture_output, text, timeout, env=None):
        self.verify()
        if capture_output is not True or text is not True or timeout != 20:
            raise MechanismError("preserved process contract changed")
        owner = self.active_owner or "cpython"
        if owner == "cpython":
            if len(command) != 2 or command[0] != sys.executable or env is not None:
                raise MechanismError("unexpected CPython reference command")
            self.program(Path(command[1]), owner)
            environment = dict(os.environ)
            gc = None
            binary_hash = _sha(Path(sys.executable).resolve())
        else:
            if len(command) != 1 or env is None or env.get("PATH") != "/nonexistent":
                raise MechanismError("native observation changed its no-fallback command")
            compile_row = next(row for row in self.result["compiles"] if row["owner"] == owner)
            if command != [str(self.root / compile_row["binary"])]:
                raise MechanismError("native observation did not execute its compiled output")
            environment, gc = env, int(env["PCC_GC_BACKEND"])
            binary_hash = _sha(Path(command[0]))
        result = self.ab._run_process(command, timeout=self.remaining(timeout), env=environment, cwd=Path(cwd))
        index = len(self.result["executions"])
        row = {"owner": owner, "gc": gc, "command": command, "returncode": result.returncode,
               "stdout": result.stdout, "stderr": result.stderr, "binary_sha256": binary_hash,
               "environment": public_environment(environment)}
        for key in ("stdout", "stderr"):
            name = "execution-" + self.lane + "-" + str(index) + "." + key
            (self.root / name).write_text(getattr(result, key), encoding="utf-8")
            row[key + "_path"] = name
        self.result["executions"].append(row)
        self.persist()
        self.verify()
        return result

    def compile_native(self, command, *, env, timeout, stderr_path):
        # The unchanged shared fixture calls this owned dependency. Preserve
        # its original 400s ceiling while tightening it to this phase's deadline.
        if (self.active_owner != "pcc1" or timeout != 400 or command[:5] !=
                [str(self.compiler), "--backend", "self", "--ir-scaffold", "on"]
                or len(command) != 8 or command[6] != "-o"
                or env.get("PCC_HOST_PYTHON") != "/usr/bin/false"
                or env.get("PCC_HOST_PCC") != "/usr/bin/false"):
            raise MechanismError("shared native compiler process contract changed")
        result = self.ab._run_process(command, timeout=self.remaining(timeout), env=env,
                                      cwd=self.source)
        Path(stderr_path).write_text(result.stderr, encoding="utf-8")
        self.native_compile = {"command": command, "returncode": result.returncode,
                               "environment": public_environment(env),
                               "stdout": result.stdout, "stderr": result.stderr}
        return result

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        if self.worker is None:
            return
        for item in items:
            path = Path(item.path)
            if path.parent != self.root / "inputs" or path.name not in ARTIFACTS:
                raise MechanismError("unexpected mechanism test source")
            item.add_marker(pytest.mark.xdist_group(name=GROUP))
            if path.name == "native_cases.py":
                self.native_module = item.module
                # Replace only the test module's subprocess binding. The
                # assertions, program, shared fixture and compiler stay intact.
                item.module.subprocess = types.SimpleNamespace(run=self.execute)
        self.origins()

    def pytest_collection_finish(self, session):
        if self.worker is None:
            return
        self.result["collected"] = [{"nodeid": item.nodeid,
            "name": item.originalname or item.name,
            "parameters": dict(item.callspec.params) if hasattr(item, "callspec") else {}}
            for item in session.items]
        expected = [row for row in expected_cases(self.root / "inputs/identity_cases.py")
                    if row["name"] in LANES[self.lane]]
        actual = [{"name": row["name"], "parameters": row["parameters"]} for row in self.result["collected"]]
        key = lambda row: json.dumps(row, sort_keys=True)
        if (sorted(map(key, actual)) != sorted(map(key, expected))
                or any(not row["nodeid"].endswith("@" + GROUP) for row in self.result["collected"])):
            raise MechanismError("explicit mechanism lane collection is incomplete or unexpected")
        self.origins()
        self.persist()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        outcome = yield
        if fixturedef.argname != "python_program_compiler":
            return
        compiler = outcome.get_result()
        owner = request.param
        if owner not in {"pcc0", "pcc1"}:
            raise MechanismError("unexpected shared compiler fixture owner")

        def compile_program(source, output, **options):
            self.verify()
            self.origins()
            if options != {"backend": "self", "ir_scaffold_mode": "on", "runtime_archive": str(self.runtime)}:
                raise MechanismError("mechanism changed strict compile options")
            self.program(source, owner)
            if owner == "pcc0":
                compiler_source = Path(inspect.getfile(compiler)).resolve(strict=True)
                relative = compiler_source.relative_to(self.source).as_posix()
                if self.payload["source_files"].get(relative, {}).get("sha256") != _sha(compiler_source):
                    raise MechanismError("host compiler fixture origin is not this candidate")
                host_origin = {"path": str(compiler_source), "sha256": _sha(compiler_source),
                               "module": compiler.__module__, "name": compiler.__name__}
            else:
                host_origin = None
            self.active_owner = owner
            compiler(source, output, **options)
            output = Path(output)
            if output.is_symlink() or not output.is_file() or self.root not in output.parents:
                raise MechanismError("fixture produced no owned native output")
            if output.read_bytes()[:4] not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
                raise MechanismError("fixture output is not native Mach-O")
            output.chmod(0o555)
            linkage = self.ab._linkage(output, timeout=self.remaining(20), env=dict(os.environ), cwd=output.parent)
            self.result["compiles"].append({"owner": owner, "binary": output.relative_to(self.root).as_posix(),
                "binary_sha256": _sha(output), "linkage": linkage, "options": options,
                "fixture_sha256": _sha(self.source / FIXTURE), "host_origin": host_origin,
                "native_compile": getattr(self, "native_compile", None) if owner == "pcc1" else None})
            self.origins()
            self.verify()
            self.persist()

        if fixturedef.cached_result is None:
            raise MechanismError("shared fixture did not register its result")
        fixturedef.cached_result = (compile_program, *fixturedef.cached_result[1:])
        outcome.force_result(compile_program)

    def pytest_runtest_setup(self, item):
        self.active_owner = None
        self.verify()

    def pytest_runtest_logreport(self, report):
        self.result["reports"].append({"nodeid": report.nodeid, "when": report.when,
            "outcome": report.outcome, "wasxfail": getattr(report, "wasxfail", None)})
        self.persist()

    def pytest_sessionfinish(self, session, exitstatus):
        if self.worker is None:
            return
        self.result["pytest_exitcode"] = int(exitstatus)
        self.verify()
        self.origins()
        if self.ab.host_python_runtime_evidence(Path(sys.executable).resolve()) != self.result["host_python_runtime"]:
            raise MechanismError("worker CPython runtime changed after use")
        self.result["status"] = "PASSED" if self.result["reports"] and exitstatus == 0 else "IDLE" if not self.result["reports"] else "FAILED"
        self.persist()
        session.config.workeroutput["candidate_mechanism"] = {
            "path": self.record_path.name, "sha256": _sha(self.record_path), "worker": self.worker}

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        if error is not None:
            raise MechanismError("candidate mechanism xdist worker failed: " + str(error))
        row = node.workeroutput.get("candidate_mechanism")
        if not isinstance(row, dict) or row.get("worker") != node.gateway.id:
            raise MechanismError("candidate mechanism worker produced no bound evidence")
        if row["worker"] in self.workers or _sha(_owned(self.root, row["path"])) != row["sha256"]:
            raise MechanismError("candidate mechanism worker evidence changed or duplicated")
        self.workers[row["worker"]] = row


def _bootstrap(request):
    global _STATE
    live = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(live))
    owner = _load(live / "scripts/run_pcc_ci_ab.py", "ci_ab_mechanism_owner")
    ab = owner._load("run_pcc_compile_ab")
    _STATE = state = Recorder(request, ab, owner)
    loaded = state.verify()
    host = ab.host_python_runtime_evidence(Path(sys.executable).resolve())
    if host != loaded["host_python_runtime"]:
        raise MechanismError("mechanism CPython runtime differs from the strict candidate producer")
    state.result["host_python_runtime"] = host
    sys.path.insert(0, str(state.source))
    from tests.python import process_timeout
    process_timeout.run_process_group_timeout = state.compile_native
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, ab._interrupt_handler)
    ab._ACTIVE_WORKER_BUDGET = state.budget
    return state


def install_fixtures(namespace):
    state = _STATE or _bootstrap(Path(os.environ["PCC_CI_AB_MECHANISM_REQUEST"]).resolve(strict=True))
    shared = _load(state.source / FIXTURE, "candidate_shared_python_fixtures")
    # Actual pytest registration retains the shared fixture's pcc0/pcc1
    # parametrization. Only artifact providers and evidence tmp_path differ.
    namespace.update(python_program_compiler=shared.python_program_compiler,
                     native_pcc1_compiler=native_pcc1_compiler,
                     pcc_runtime_archive=pcc_runtime_archive, tmp_path=tmp_path)


def configure(config):
    worker = getattr(config, "workerinput", None)
    if worker is None:
        if config.getoption("numprocesses") != 6 or config.getoption("dist") != "loadgroup":
            raise MechanismError("candidate mechanism requires six xdist workers and its evidence group")
    elif (worker.get("workercount") != 6 or worker.get("workerid") != _STATE.worker
          or not config.getoption("loadgroup")):
        raise MechanismError("candidate mechanism worker identity/count is invalid")
    _STATE.result["worker_identity"] = {"worker": _STATE.worker, "pid": os.getpid(),
                                      "workercount": 6, "lane": _STATE.lane, "request_sha256": _sha(_STATE.request)}
    config.pluginmanager.register(_STATE, "candidate-mechanism-recorder")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    request = Path(args.request).resolve(strict=True)
    sys.modules["pcc_ci_ab_mechanism"] = sys.modules[__name__]
    os.environ["PCC_CI_AB_MECHANISM_REQUEST"] = str(request)
    aggregate = None
    for lane in LANES:
        os.environ["PCC_CI_AB_MECHANISM_LANE"] = lane
        state = _bootstrap(request)
        try:
            selections = [str(state.root / "inputs/identity_cases.py")] if lane == "ordinary" else [
                str(state.root / "inputs/native_cases.py") + "::" + (REFERENCE if lane == "reference" else NATIVE)]
            code = pytest.main([
                "-x", "-vv", "--assert=rewrite", "-p", "no:cacheprovider", "-p", "xdist.plugin",
                "-n", "6", "--dist", "loadgroup",
                "-c", str(state.root / "inputs/pytest.ini"), "--confcutdir", str(state.root / "inputs"),
                "--basetemp", str(state.root / ("pytest-work-" + lane)), *selections,
            ])
            if code != 0 or set(state.workers) != {"gw" + str(n) for n in range(6)}:
                raise MechanismError("six-worker candidate mechanism lane did not complete")
            results = {name: json.loads(_owned(state.root, row["path"]).read_text())
                       for name, row in state.workers.items()}
            active = [name for name, row in results.items() if row["reports"]]
            if len(active) != 1:
                raise MechanismError("mechanism lane evidence group did not have one execution owner")
            result = results[active[0]]
            if result["reports"] != state.result["reports"]:
                raise MechanismError("controller and actual worker test reports disagree")
            result["xdist"] = {"workers": 6, "dist": "loadgroup", "group": GROUP,
                                "executing_worker": active[0], "worker_results": state.workers}
            state.result["pytest_exitcode"] = int(code)
            state.result["completed_workers"] = state.workers
            state.persist()
            result["controller"] = {"path": state.record_path.name, "sha256": _sha(state.record_path)}
            result["selection"] = selections
            state.verify()
            validate_worker_evidence(result, lane=lane, payload=state.payload, root=state.root, aggregate=result)
            lane_path = state.root / ("lane-" + lane + ".json")
            _write(lane_path, result)
            if aggregate is None:
                aggregate = {key: value for key, value in result.items()
                             if key not in {"xdist", "selection", "lane", "worker_identity"}}
                for key in ("collected", "reports", "executions", "compiles", "programs"):
                    aggregate[key] = []
                aggregate["lanes"] = {}
            for key in ("collected", "reports", "executions", "compiles", "programs"):
                aggregate[key].extend(result[key])
            aggregate["import_origins"].update(result["import_origins"])
            aggregate["lanes"][lane] = {"path": lane_path.name, "sha256": _sha(lane_path)}
        except BaseException as exc:
            state.result.update(status="FAILED", error=type(exc).__name__ + ": " + str(exc))
            state.persist()
            raise
        finally:
            state.ab._ACTIVE_WORKER_BUDGET = None
    state.verify()
    validate_result(aggregate, request=request, root=state.root)
    _write(state.root / "result.json", aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real host and native prior scheduling; run under a hard tree watchdog."""

from collections import deque
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import site
import sys
from types import SimpleNamespace

import pytest

from pcc.frontends.python import worker_resource_plan as policy
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests/fixtures/native/worker_size_priors.py"
CASES = ("width", "growth")


def _exception_snapshot(error):
    # Retain only the innermost frames, never source lines, locals, globals,
    # exception notes or chained messages (which may contain credentials).
    if not issubclass(type(error), BaseException):
        raise TypeError("exception context requires a BaseException")
    frames = deque(maxlen=16)
    count = 0
    text_truncated = False
    # Read built-in slots directly; exception/metaclass attribute overrides
    # must not replace the original error while its receipt is being saved.
    traceback = BaseException.__traceback__.__get__(error)
    while traceback is not None:
        code = traceback.tb_frame.f_code
        text_truncated |= len(code.co_filename) > 512 or len(code.co_name) > 128
        frames.append({
            "filename": code.co_filename[-512:], "lineno": traceback.tb_lineno,
            "name": code.co_name[-128:],
        })
        count += 1
        traceback = traceback.tb_next
    name = type.__dict__["__name__"].__get__(type(error))
    return {
        "type": name[:128], "traceback": list(frames),
        "frames_truncated": count > len(frames),
        "text_truncated": text_truncated or len(name) > 128,
    }


def _exception_context(error):
    record = _exception_snapshot(error)
    chain = []
    seen = {id(error)}
    chain_truncated = chain_cycle_detected = False
    while True:
        # Match displayed exception chaining: an explicit cause takes priority,
        # and ``raise ... from None`` suppresses an implicit context. Cleanup
        # errors can retain the original interruption here without changing
        # which exception propagates or reading any chained error messages.
        cause = BaseException.__cause__.__get__(error)
        if cause is not None:
            linked, relation = cause, "cause"
        elif not BaseException.__suppress_context__.__get__(error):
            linked, relation = BaseException.__context__.__get__(error), "context"
        else:
            break
        if not issubclass(type(linked), BaseException):
            break
        if id(linked) in seen:
            chain_cycle_detected = True
            break
        if len(chain) == 8:
            chain_truncated = True
            break
        seen.add(id(linked))
        chain.append({"relation": relation, **_exception_snapshot(linked)})
        error = linked
    # Preserve the existing unchained receipt shape exactly.
    if chain or chain_truncated or chain_cycle_detected:
        record.update({
            "chain": chain, "chain_truncated": chain_truncated,
            "chain_cycle_detected": chain_cycle_detected,
        })
    return record


def _fixture_failure_evidence(directory):
    # Only this synthetic fixture's bounded protocol files are copied into
    # execution.json, which the existing CI artifact pattern already keeps.
    # Tokens here identify synthetic attempts, never user credentials.
    names = ["budget.tsv", "admission.tsv", "peer-report-ready", "windows-stage1-phase"]
    for index in range(3):
        names.extend(["rss" + str(index), "attempts" + str(index)])
        for attempt in (1, 2):
            suffix = str(index) + "." + str(attempt)
            names.extend(["started." + suffix, "complete." + suffix])
    evidence = {}
    for name in names:
        try:
            with (directory / name).open("r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(8193)
            evidence[name] = {"text": text[:8192], "truncated": len(text) > 8192}
        except FileNotFoundError:
            continue
        except OSError as error:
            evidence[name] = {"read_error": type(error).__name__ + ": " + str(error)}
    return evidence


def _fixture_diagnostic_report(text):
    """Parse the bounded fixture report independently of the runtime reader."""
    if type(text) is not str or len(text) > 384:
        raise ValueError("invalid diagnostic report text")
    fields = text.splitlines()
    if len(fields) != 6 or fields[0] != policy.RESOURCE_REPORT_SCHEMA:
        raise ValueError("invalid diagnostic report schema")
    pid, current, peak = int(fields[1]), int(fields[3]), int(fields[4])
    phase, token = fields[2], fields[5]
    if (pid <= 0 or not 0 < current <= peak or max(pid, current, peak) > 9223372036854775807
            or fields[1] != str(pid) or fields[3] != str(current) or fields[4] != str(peak)
            or phase not in ("started", "complete") or len(token) != 64
            or any(char not in "0123456789abcdef" for char in token)):
        raise ValueError("invalid diagnostic report values")
    return pid, token, phase, current, peak


def _fixture_resource_diagnostics(directory):
    """Match bounded retained publications, never the overwritten RSS file.

    Raw values are the two boundary results used by that publication, not an
    independently sampled /proc oracle. Duplicate text with differing raw
    values is ambiguous. Missing seals, killed children and bounds are visible.
    Diagnostic work changes fixture RSS/timing and is not performance evidence.
    """
    names = ["resource-driver.tsv"]
    identities = {}
    child_journals = {}
    protocol_errors = []
    for index in range(3):
        for attempt in (1, 2):
            marker = directory / ("started." + str(index) + "." + str(attempt))
            try:
                with marker.open("rb") as stream:
                    raw_marker = stream.read(513)
                fields = raw_marker.decode("utf-8").split("\t")
                if len(raw_marker) > 512 or len(fields) != 4:
                    raise ValueError("invalid started marker")
                pid, token = int(fields[0]), fields[1]
                if (pid <= 0 or str(pid) != fields[0] or len(token) != 64
                        or any(char not in "0123456789abcdef" for char in token)
                        or fields[2] not in ("pcc", "cpython") or fields[3] not in ("0", "1", "2", "3", "4")):
                    raise ValueError("invalid started identity")
                name = "resource-child-" + str(index) + "-" + token + ".tsv"
                if name in names or (pid, token) in identities.values():
                    raise ValueError("duplicate started identity")
                names.append(name)
                child_journals[name] = (pid, token)
                if attempt == 1:
                    identities[index] = (pid, token)
                else:
                    protocol_errors.append("unexpected width retry")
            except FileNotFoundError:
                if attempt == 1:
                    protocol_errors.append("missing started identity " + str(index))
            except (OSError, UnicodeError, ValueError):
                protocol_errors.append("invalid started identity " + str(index))
    if len({pid for pid, token in identities.values()}) != len(identities):
        protocol_errors.append("duplicate started PID")
    if len({token for pid, token in identities.values()}) != len(identities):
        protocol_errors.append("duplicate started token")
    journals = {}
    publications = {}
    accepted = []
    observations = []
    driver_events = []
    published_phases = set()
    child_phases = {name: [] for name in child_journals}
    publication_peaks = {name: 0 for name in child_journals}
    for name in names:
        entry = {"complete": False}
        journals[name] = entry
        try:
            with (directory / name).open("rb") as stream:
                raw = stream.read(262145)
            entry["physical_bytes_read"] = len(raw)
            entry["truncated"] = len(raw) > 262144
            text = raw[:262144].decode("utf-8")
            entry["text"] = text
            if entry["truncated"]:
                entry["reason"] = "limit"
                continue
            lines = text.splitlines()
            physical_lines = raw.splitlines(keepends=True)
            if (len(lines) > 256 or any(len(line) > 1024 for line in physical_lines)
                    or b"\r" in raw):
                entry["reason"] = "limit"
                continue
            try:
                with (directory / (name + ".status")).open("rb") as stream:
                    status_raw = stream.read(1025)
                status_text = status_raw.decode("utf-8")
                if len(status_raw) > 1024 or not status_raw.endswith(b"\n") or b"\r" in status_raw:
                    entry["reason"] = "status-limit"
                else:
                    count, size, incomplete = ast.literal_eval(status_text)
                    entry["reason"] = incomplete or ""
                    entry["complete"] = (type(count) is int and type(size) is int
                                         and type(incomplete) is str and not incomplete
                                         and count == len(lines)
                                         and size == len(raw)
                                         and text.endswith("\n"))
                    if not entry["complete"] and not entry["reason"]:
                        entry["reason"] = "count-or-size-mismatch"
            except (OSError, ValueError, SyntaxError, TypeError):
                entry["reason"] = "missing-or-invalid-status"
            for line in lines:
                row = ast.literal_eval(line)
                if type(row) is not tuple or len(row) != 2 or type(row[0]) is not str:
                    raise ValueError("invalid journal record shape")
                event, payload = row
                if event == "published" and name in child_journals:
                    if type(payload) is not tuple or len(payload) not in (4, 5):
                        raise ValueError("invalid publication shape")
                    report_text, current, raw_peak, peak = payload[:4]
                    previous_peak = payload[4] if len(payload) == 5 else 0
                    identity = _fixture_diagnostic_report(report_text)
                    if (any(type(value) is not int or not -9223372036854775808 <= value <= 9223372036854775807
                            for value in payload[1:]) or identity[:2] != child_journals[name]
                            or identity[3:] != (current, peak)
                            or max(current, raw_peak, previous_peak) != peak
                            or (len(payload) == 5 and previous_peak != publication_peaks[name])):
                        raise ValueError("invalid publication binding")
                    # Five fields explicitly bind retained high water to this
                    # child's preceding publication. Four fields remain the
                    # historical raw-sample format, including old decreases.
                    publication_peaks[name] = peak
                    phase_key = identity[:3]
                    if phase_key in published_phases:
                        protocol_errors.append("duplicate width publication phase")
                    published_phases.add(phase_key)
                    child_phases[name].append(identity[2])
                    publications.setdefault(report_text, []).append(payload[1:])
                elif event == "accepted" and name == "resource-driver.tsv":
                    if (type(payload) is not tuple or len(payload) != 6
                            or type(payload[0]) is not int or type(payload[1]) is not str
                            or type(payload[2]) is not str or type(payload[3]) is not int
                            or type(payload[4]) is not int):
                        raise ValueError("invalid acceptance shape")
                    if (_fixture_diagnostic_report(payload[5]) != payload[:5]
                            or payload[:2] not in identities.values()):
                        raise ValueError("invalid acceptance binding")
                    accepted.append(payload)
                    driver_events.append((event, payload))
                elif event == "observation" and name == "resource-driver.tsv":
                    if type(payload) is not tuple or len(payload) != 4:
                        raise ValueError("invalid observation shape")
                    observations.append(payload)
                    driver_events.append((event, payload))
                else:
                    raise ValueError("unknown diagnostic event")
        except (OSError, ValueError, SyntaxError, TypeError):
            entry["complete"] = False
            entry["reason"] = "missing-or-invalid-journal"
    if any(phases != ["started", "complete"] for phases in child_phases.values()):
        protocol_errors.append("missing/duplicate/out-of-order width publication phases")
    matches = []
    for pid, token, phase, current, peak, report_text in accepted:
        candidates = publications.get(report_text, [])
        raw = set(candidates)
        binding = report_text.splitlines()
        valid = (len(binding) == 6 and binding[0] == policy.RESOURCE_REPORT_SCHEMA
                 and binding[1] == str(pid) and binding[2] == phase
                 and binding[3] == str(current) and binding[4] == str(peak)
                 and binding[5] == token)
        state = "unmatched" if not candidates else "ambiguous" if len(raw) != 1 else "matched"
        # Candidate rows entered this table only after the per-child raw and
        # previous-publication binding above; never infer prior peak here.
        if not valid or any(row[0] != current or row[2] != peak
                            for row in candidates):
            state = "invalid-binding"
        matches.append({
            "pid": pid, "token": token, "phase": phase, "current": current, "peak": peak,
            "report_sha256": hashlib.sha256(report_text.encode("utf-8")).hexdigest(),
            "state": state, "raw_candidates": sorted(raw), "publications": len(candidates),
        })
    # Correlate the actual pool sample to accepted reads and the independent
    # existing retirement trace. Merely having valid journal seals is not a
    # verified completion chain. Retain partial evidence on every mismatch.
    retires = {}
    starts = {}
    try:
        with (directory / "admission.tsv").open("rb") as stream:
            raw_admission = stream.read(8193)
        if len(raw_admission) > 8192 or not raw_admission.endswith(b"\n"):
            raise ValueError("admission limit or partial row")
        rows = raw_admission.decode("utf-8").splitlines()
        if len(rows) > 32:
            raise ValueError("admission row limit")
        for row in rows:
            fields = row.split("\t")
            if len(fields) != 7 or fields[0] not in ("start", "retire"):
                raise ValueError("unexpected width admission row")
            index, pid, peak = int(fields[1]), int(fields[2]), int(fields[5])
            target = starts if fields[0] == "start" else retires
            if index not in identities or identities[index][0] != pid or index in target:
                raise ValueError("missing/mismatched/duplicate admission identity")
            if fields[0] == "start" and peak != 0:
                raise ValueError("nonzero initial peak")
            if fields[0] == "retire" and (index not in starts or peak <= 0):
                raise ValueError("retire before start or invalid peak")
            target[index] = peak
        if set(starts) != set(range(3)) or set(retires) != set(range(3)):
            raise ValueError("missing start or retirement")
    except (OSError, UnicodeError, ValueError):
        protocol_errors.append("invalid or incomplete admission trace")
    by_attempt = {identity: [] for identity in identities.values()}
    seen_observations = set()
    verified = []
    for event, payload in driver_events:
        if event == "accepted":
            pid, token, phase, current, peak, report_text = payload
            key = (pid, token)
            if (type(pid) is not int or type(token) is not str or key not in by_attempt
                    or key in seen_observations or phase not in ("started", "complete")
                    or type(current) is not int or type(peak) is not int or not 0 < current <= peak):
                protocol_errors.append("invalid accepted attempt or post-retirement acceptance")
            else:
                if phase == "started" and any(previous == "complete" for previous, value in by_attempt[key]):
                    protocol_errors.append("started acceptance after complete")
                by_attempt[key].append((phase, peak))
            continue
        valid = type(payload) is tuple and len(payload) == 4
        if valid:
            index, pid, token, sample = payload
            valid = (type(index) is int and type(pid) is int and type(token) is str
                     and index in identities and identities[index] == (pid, token)
                     and type(sample) is tuple and len(sample) == 4)
        if valid:
            inputs, peak, binding = sample[1:]
            valid = (sample[0] == "prior-component:" + str(index)
                     and type(inputs) is list and len(inputs) == 6
                     and all(type(value) is int and 0 <= value <= 9223372036854775807 for value in inputs)
                     and inputs[0] == inputs[1] and inputs[2:] == [0, 0, 0, 1]
                     and type(peak) is int and peak > 0
                     and type(binding) is tuple and len(binding) == 2
                     and binding[0] == "host-export-v1"
                     and type(binding[1]) is int and 0 < binding[1] <= 9223372036854775807)
        if not valid:
            protocol_errors.append("invalid observation shape or attempt identity")
            continue
        key = (pid, token)
        history = by_attempt[key]
        if (key in seen_observations or not history or history[-1][0] != "complete"
                or peak != max(value for phase, value in history) or retires.get(index) != peak):
            protocol_errors.append("duplicate/missing/mismatched observation maximum or retirement")
        else:
            verified.append({"index": index, "pid": pid, "token": token,
                             "accepted_maximum": peak, "retire_peak": retires[index]})
        seen_observations.add(key)
    if len(verified) != 3 or seen_observations != set(identities.values()):
        protocol_errors.append("missing verified retirement-observation chain")
    return {"scope": "fixture_case_pool_not_observation_scope", "journals": journals,
            "claim": "complete validates width evidence chain, not the fixture result",
            "complete": (all(item["complete"] for item in journals.values())
                         and all(item["state"] == "matched" for item in matches)
                         and not protocol_errors),
            "accepted": matches, "observations": observations,
            "verified_observations": verified, "protocol_errors": protocol_errors,
            "measurement_effect": "enabled journal allocations and I/O perturb RSS and timing"}


def _environment(owner, collector):
    environment = dict(os.environ)
    environment.pop("PCC_TEST_WINDOWS_STAGE1_PHASE", None)
    # Keep the outer watchdog's budget. As in the existing resource driver,
    # its synchronized state is not falsely presented as this child's owner
    # state; this component uses the policy's local driver/worker accounting.
    assert int(environment.get("PCC_WORKER_TREE_BUDGET_BYTES", "0")) > 0
    for key in (policy.TREE_STATE_ENV, policy.RESOURCE_REPORT_ENV, policy.RESOURCE_TOKEN_ENV):
        environment.pop(key, None)
    environment.update({
        "PCC_TEST_RESOURCE_OWNER": owner,
        "PCC_GC_BACKEND": str(collector),
        "PCC_PY_FRONTEND_WORKER_TIMING": "0",
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PCC_TEST_NO_NATIVE_PROVISIONING": "1",
        "PCC_NO_AUTO_PCC1": "1",
    })
    if owner == "pcc":
        environment.update({
            "PATH": "", "PCC_HOST_PYTHON": "/nonexistent/host-python",
            "PCC_HOST_PCC": "/nonexistent/host-pcc",
            "PCC_RUNTIME_CC": "/nonexistent/runtime-compiler",
        })
    return environment


def _run(prefix, case, directory, owner, collector, compiler_parameter=None):
    directory.mkdir()
    environment = _environment(owner, collector)
    windows_phase = (sys.platform == "win32" and compiler_parameter == "pcc0"
                     and owner == "pcc" and collector == 0 and case == "width")
    if windows_phase:
        environment["PCC_TEST_WINDOWS_STAGE1_PHASE"] = "1"
    command = [*prefix, case, str(directory)]
    result = run_process_group_timeout(command, env=environment, timeout=20)
    (directory / "stdout").write_text(result.stdout)
    (directory / "stderr").write_text(result.stderr)
    record = {
        "owner": owner, "requested_collector": collector, "case": case,
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "outer_tree_budget_bytes": int(environment["PCC_WORKER_TREE_BUDGET_BYTES"]),
        "accounting_scope": "local_driver_and_workers_under_outer_tree_watchdog",
        "native_execution": owner == "pcc", "pcc1_stage2_dispatch": False,
        "windows_stage1_phase_witness": windows_phase,
    }
    try:
        record["resource_diagnostics"] = (
            _fixture_resource_diagnostics(directory) if case == "width"
            else {"enabled": False, "scope": "width-only"}
        )
    except Exception:
        # Evidence parsing must never replace the original execution outcome.
        record["resource_diagnostics"] = {"complete": False, "reason": "receipt-error"}
    if (result.returncode, result.stdout, result.stderr) != (
        0, "WORKER_SIZE_PRIOR_OK " + case + "\n", "",
    ):
        record["failure_evidence"] = _fixture_failure_evidence(directory)
    (directory / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
    # A string message is printed whole; pytest would saferepr-truncate a dict
    # at default verbosity and hide the child's stderr.
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "WORKER_SIZE_PRIOR_OK " + case + "\n", "",
    ), json.dumps(record, indent=2)
    assert (directory / "complete").read_text() == owner + "\t" + str(collector)
    expected_attempts = 3 if case == "width" else 4
    starts = sorted(directory.glob("started.*"))
    assert len(starts) == expected_attempts
    admissions = [line.split("\t") for line in (directory / "admission.tsv").read_text().splitlines()
                  if line.startswith("start\t")]
    assert len(admissions) == expected_attempts
    actual_collectors = []
    pids, tokens = [], []
    for path in starts:
        fields = path.read_text().split("\t")
        assert len(fields) == 4 and fields[2] == owner
        pids.append(int(fields[0]))
        tokens.append(fields[1])
        actual_collectors.append(int(fields[3]))
        _, index, attempt = path.name.split(".")
        launched = [row for row in admissions if row[1] == index]
        assert int(launched[int(attempt) - 1][2]) == int(fields[0]), "launcher PID differs from worker PID"
    assert len(set(pids)) == expected_attempts and len(set(tokens)) == expected_attempts
    assert actual_collectors == [collector] * expected_attempts
    record["observed_child_collectors"] = actual_collectors
    record["child_pids"] = pids
    record["status"] = "PASS"
    (directory / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def _check_marker_read_retry(tmp_path):
    # Execute the real helper bodies without the fixture's unconditional main.
    # These host-only injected faults do not claim a Windows filesystem run.
    names = {"read", "exists", "wait_file"}
    nodes = [node for node in ast.parse(DRIVER.read_text(encoding="utf-8")).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    code = compile(ast.Module(body=nodes, type_ignores=[]), str(DRIVER), "exec")
    marker = tmp_path / "marker"

    def waiter(faults, wake_times, publish_on_sleep=False):
        faults, wake_times = iter(faults), iter(wake_times)
        now, sleeps, reads = [100.0], [], []

        def sleep(seconds):
            assert seconds == 0.005
            sleeps.append(seconds)
            now[0] = next(wake_times)
            if publish_on_sleep and len(sleeps) == 1:
                marker.write_text("peer ready", encoding="utf-8")

        def open_marker(path, mode, *, encoding):
            assert Path(path) == marker
            assert (mode, encoding) == ("r", "utf-8")
            reads.append(path)
            error = next(faults, None)
            if error is not None:
                raise error
            return open(path, mode, encoding=encoding)

        namespace = {"os": os, "open": open_marker,
                     "time": SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)}
        exec(code, namespace)
        return namespace["wait_file"], sleeps, reads

    marker.write_text("peer ready", encoding="utf-8")
    denied = PermissionError(13, "synthetic sharing violation", str(marker))
    wait, sleeps, reads = waiter([denied, denied], [114.990, 114.995])
    assert wait(str(tmp_path), "marker") == "peer ready"
    assert len(reads) == 3 and sleeps == [0.005, 0.005]

    # A permanent denial keeps its original exception and the original 15 s
    # deadline, even when the marker first appears near expiry. Neither
    # successful stat nor each retry may reset that deadline.
    marker.unlink()
    wait, sleeps, reads = waiter([denied, denied], [114.999, 115.0], True)
    with pytest.raises(PermissionError) as caught:
        wait(str(tmp_path), "marker")
    assert caught.value is denied
    assert len(reads) == 2 and sleeps == [0.005, 0.005]

    unrelated = OSError(5, "synthetic I/O error", str(marker))
    wait, sleeps, reads = waiter([unrelated], [])
    with pytest.raises(OSError) as caught:
        wait(str(tmp_path), "marker")
    assert caught.value is unrelated and len(reads) == 1 and sleeps == []

    marker.write_text("", encoding="utf-8")
    wait, sleeps, reads = waiter([], [])
    assert wait(str(tmp_path), "marker") == ""
    assert len(reads) == 1 and sleeps == []

    marker.unlink()
    wait, sleeps, reads = waiter([], [114.999, 115.0])
    with pytest.raises(AssertionError, match="missing worker marker: marker"):
        wait(str(tmp_path), "marker")
    assert reads == [] and sleeps == [0.005, 0.005]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.pcc_gate(env="PCC_WORKER_TREE_BUDGET_BYTES")
def test_size_prior_real_host_processes(case, tmp_path):
    if case == "width":
        _check_marker_read_retry(tmp_path)
    _run([sys.executable, "-B", str(DRIVER)], case, tmp_path / case, "cpython", 0)


@pytest.mark.pcc_gate(env="PCC_WORKER_TREE_BUDGET_BYTES")
def test_resource_host_self_spawn_preserves_interpreter_identity_and_venv(tmp_path):
    script = tmp_path / "host_identity.py"
    script.write_text('''
import json
import os
from pathlib import Path
import shlex
import site
import sys

import pytest
from pcc.frontends.python import worker_process_pool as pool


def main():
    root, depth = Path(sys.argv[1]), int(sys.argv[2])
    sites = [os.path.normcase(path) for path in site.getsitepackages()]
    record = {
        "pid": os.getpid(), "executable": sys.executable,
        "base_executable": sys._base_executable,
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "site_packages": [path for path in sys.path if os.path.normcase(path) in sites],
        "pytest_origin": pytest.__file__,
        "environment_value": os.environ["PCC_TEST_ENVIRONMENT_VALUE"],
    }
    if depth < 2:
        command = shlex.join([sys.executable, "-B", __file__, str(root), str(depth + 1)])
        pid = pool._start_resource_worker([pool._command_spec(command)], 0)
        record["child_pid"] = pid
        try:
            assert pool._HOST_WORKERS[pid].wait(timeout=6) == 0
            pool._retire_resource_worker(pid)
        finally:
            if pid in pool._HOST_WORKERS:
                pool._stop_resource_worker(pid)
        assert pool._HOST_WORKERS == {}
    (root / ("identity." + str(depth) + ".json")).write_text(json.dumps(record))


main()
''', encoding="utf-8")
    environment = _environment("cpython", 0)
    environment["PCC_TEST_ENVIRONMENT_VALUE"] = "unchanged= value"
    result = run_process_group_timeout(
        [sys.executable, "-B", str(script), str(tmp_path), "0"],
        env=environment, timeout=20,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result
    identities = [json.loads((tmp_path / ("identity." + str(depth) + ".json")).read_text())
                  for depth in range(3)]
    sites = [os.path.normcase(path) for path in site.getsitepackages()]
    expected = {
        "executable": sys.executable, "base_executable": sys._base_executable,
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "site_packages": [path for path in sys.path if os.path.normcase(path) in sites],
        "pytest_origin": pytest.__file__, "environment_value": "unchanged= value",
    }
    assert expected["site_packages"], "host dependency path must be exercised"
    assert len({record["pid"] for record in identities}) == 3
    for depth, record in enumerate(identities):
        assert {key: record[key] for key in expected} == expected
        if depth < 2:
            assert record["child_pid"] == identities[depth + 1]["pid"]


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    (sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64", "aarch64", "arm64"))
    or (sys.platform == "darwin" and platform.machine().lower() == "arm64")
    or (sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))
))
def test_size_prior_native_processes_five_collectors(
    tmp_path, explicit_owned_runtime, python_program_compiler, request, capfd,
):
    """Execute the actual policy and same-binary children on each host target.

    Runtime construction and compiler provisioning are forbidden by the shared
    fixture. This proves a native resource component, not pcc1 Stage2 dispatch.
    Observed collector IDs come from the owned ABI in every actual child.
    """
    binary = tmp_path / ("prior-worker.exe" if sys.platform == "win32" else "prior-worker.out")
    receipt_path = tmp_path / "native-worker-size-priors.json"
    receipt = {
        "status": "COMPILING", "platform": sys.platform,
        "machine": platform.machine(),
        "compiler_parameter": request.node.callspec.params["python_program_compiler"],
        "compiler_module": python_program_compiler.__module__,
        "backend": "self", "libpython": "off", "ir_scaffold": "on",
        "source_sha256": hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
        "runtime_archive_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
        "scope": "native prior/LPT resource component with real self-spawned children",
        "pcc1_stage2_dispatch": False, "executions": [],
    }

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        python_program_compiler(
            str(DRIVER), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
        )
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt["exception_context"] = _exception_context(error)
        save()
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compile.stdout").write_text(captured.out)
        (tmp_path / "compile.stderr").write_text(captured.err)
    contents = binary.read_bytes()
    assert (contents.startswith(b"MZ") if sys.platform == "win32" else
            contents.startswith(b"\xcf\xfa\xed\xfe") if sys.platform == "darwin" else
            contents.startswith(b"\x7fELF")), "unexpected native executable format"
    receipt["binary_sha256"] = hashlib.sha256(contents).hexdigest()
    receipt["status"] = "RUNNING"
    save()
    for collector in range(5):
        for case in CASES:
            try:
                result = _run(
                    [str(binary)], case, tmp_path / ("gc" + str(collector) + "-" + case),
                    "pcc", collector, request.node.callspec.params["python_program_compiler"],
                )
            except BaseException as error:
                receipt["status"] = "NATIVE_EXECUTION_FAILED"
                receipt["failed_case"] = case
                receipt["failed_collector"] = collector
                receipt["error"] = type(error).__name__ + ": " + str(error)
                receipt["exception_context"] = _exception_context(error)
                save()
                raise
            receipt["executions"].append(result)
            save()
    assert len(receipt["executions"]) == 10
    receipt["status"] = "PASS"
    save()

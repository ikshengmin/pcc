"""Opt-in host-only profile of one cli_bootstrap x86 assembly call.

Only the nominated ordinary Stage1 worker imports PCC. No production module
imports this adapter. The profiler observes Python and C calls: its overhead
and internal memory are not bounded by the bounded report. This is never a
baseline performance measurement. A killed attempt may leave only claim.json.
"""

import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys

MODULE = "pcc.driver.cli_bootstrap"
TARGET = "x86_64-unknown-linux-gnu"
ROUTE = "indexed-assembly"
FLAG = "PCC_HOST_ASSEMBLY_PROFILE"
CONFIG = "PCC_HOST_ASSEMBLY_PROFILE_CONFIG"
SOURCE_ROOT = "PCC_HOST_ASSEMBLY_PROFILE_ROOT"
SCHEMA = "pcc.host-assembly-function-profile.v1"
MANIFEST_LIMIT = 1_048_576
REPORT_LIMIT = 65_536
TOP = 20
EDGE_LIMIT = 12
# A failed 3.13 profiler can have destructive deallocation side effects.
# Keep at most this build's one claimed instance alive; do not pretend that
# public monitoring APIs can recover arbitrary partial enable/disable failure.
_FAILED_PROFILER = None
OWNERS = ("pcc.backend.x86_64_asm_driver", "pcc.backend.indexed_phase_timing",
          "pcc.backend.x86_64_encode", "pcc.backend.elf_x86_64")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path, value):
    # Exclusive, bounded publications never replace evidence from a retry.
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    if len(payload.encode("ascii")) > REPORT_LIMIT:
        raise ValueError("assembly profile report exceeds its byte cap")
    with Path(path).open("x", encoding="ascii") as stream:
        stream.write(payload)
        stream.flush()


def _observer_conflict():
    # CPython 3.13 cProfile uses PEP 669, independently of sys.getprofile().
    try:
        return (sys.getprofile() is not None
                or sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is not None)
    except BaseException:
        return True


def prepare_environment(root, directory, environment, source, runtime, compiler_sha256, target):
    """Configure only the existing Stage1 command; never launch a process."""
    if environment.get(FLAG) != "1":
        return environment
    directory = Path(directory)
    owned = False
    status = "SETUP_FAILED"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        owned = True
        status = "UNSUPPORTED_HOST"
        if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
                or sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64")
                or target != TARGET):
            raise ValueError(status)
        status = "PROFILE_HOOK_CONFLICT"
        if _observer_conflict():
            raise ValueError(status)
        # Before adding any path: never shadow an existing startup hook.
        # The cwd check covers the script-orchestrator versus child -m path.
        status = "SITECUSTOMIZE_CONFLICT"
        if ("sitecustomize" in sys.modules
                or importlib.util.find_spec("sitecustomize") is not None
                or importlib.machinery.PathFinder.find_spec("sitecustomize", [str(root)]) is not None):
            raise ValueError(status)
        status = "STARTUP_PATH_CONFLICT"
        if environment.get("PYTHONPATH", "") != os.environ.get("PYTHONPATH", ""):
            raise ValueError(status)
        status = "SETUP_FAILED"
        adapter = Path(__file__).read_bytes()
        (directory / "_pcc_host_assembly_profile.py").write_bytes(adapter)
        (directory / "sitecustomize.py").write_text(
            "from _pcc_host_assembly_profile import install\ninstall()\n", encoding="ascii")
        identity = {
            "source": source,
            "runtime": {key: value for key, value in runtime.items() if key.endswith("sha256")},
            "python": {"implementation": "cpython", "version": list(sys.version_info),
                       "executable_sha256": compiler_sha256},
            "target": target, "module": MODULE,
            "adapter_sha256": hashlib.sha256(adapter).hexdigest(),
            "owner_sources": {name.replace(".", "/") + ".py":
                              _digest(Path(root) / (name.replace(".", "/") + ".py"))
                              for name in (*OWNERS, MODULE)},
        }
        # Paths stay in the child environment, not uploaded diagnostic JSON.
        _write(directory / "setup.json", {"identity": identity})
        result = environment.copy()
        result[CONFIG] = str(directory / "setup.json")
        result[SOURCE_ROOT] = str(root)
        result["PYTHONPATH"] = str(directory) + os.pathsep + environment.get("PYTHONPATH", "")
        return result
    except BaseException:
        # Conflicts disable only observation, never the ordinary Stage1 job.
        if owned:
            try:
                _write(directory / "install-status.json", {"schema": SCHEMA, "status": status,
                                                           "module": MODULE, "target": TARGET})
            except BaseException:
                pass
        return environment


def _admit(argv, root):
    """Read only the current bounded ordinary manifest, not the PCC parser."""
    if (len(argv) != 5 or argv[:4] != [sys.executable, "-m", "pcc",
                                        "--pcc-python-multi-codegen-worker"]):
        return None
    with open(argv[4], "rb") as stream:
        raw = stream.read(MANIFEST_LIMIT + 1)
    if len(raw) > MANIFEST_LIMIT:
        return None
    lines = raw.decode("utf-8").splitlines()
    # Checkpoint v5 is deliberately outside this normal-Stage1 measurement.
    if (len(lines) < 14 or lines[0] != "pcc.frontends.python.codegen_worker.v4"
            or lines[4] != "codegen" or lines[7:9] != ["off", "on"]):
        return None
    siblings = int(lines[10])
    if not 0 <= siblings <= 4096:
        return None
    position = 11 + siblings
    count = int(lines[position])
    position += 1
    if not 1 <= count <= 4096 or len(lines) != position + count + 2:
        return None
    if lines[position + count] != "1":
        return None
    assigned = int(lines[position + count + 1])
    if not 0 <= assigned < count:
        return None
    row = lines[position + assigned].split("\t")
    if (len(row) != 3 or row[:2] != [str(assigned), MODULE]
            or Path(row[2]).resolve() != Path(root) / "pcc/driver/cli_bootstrap.py"):
        return None
    return hashlib.sha256(raw).hexdigest()


def _safe(text):
    text = re.sub(r"0x[0-9a-fA-F]+", "address", text)
    return re.sub(r"[^a-zA-Z0-9_./:<> -]", "_", text)[:160]


def _labels(modules):
    # IDs, not code equality or pstats (filename, line, name) keys. In
    # particular, different dataclass __init__ functions can share <string>:2.
    labels = {}
    for module in modules:
        for name, value in vars(module).items():
            if isinstance(value, type) and value.__module__ == module.__name__:
                code = getattr(vars(value).get("__init__"), "__code__", None)
                if code is not None:
                    labels[id(code)] = module.__name__ + "." + name + ".__init__"
    return labels


def _summary(entries, root, labels, key_codes):
    rows, ids, all_edges = [], {}, []
    for entry in entries:
        code = entry.code
        key = id(code)
        ids[key] = len(rows)
        if isinstance(code, str):
            name, path, line = _safe(code), "<builtin>", 0
        else:
            name = _safe(labels.get(key, code.co_qualname))
            try:
                path = str(Path(code.co_filename).resolve().relative_to(root))
            except ValueError:
                path = "<generated>" if code.co_filename.startswith("<") else "<external>"
            path, line = _safe(path), code.co_firstlineno
        rows.append({"id": ids[key], "name": name, "file": path, "line": line,
                     "calls": entry.callcount, "recursive_calls": entry.reccallcount,
                     "self_s": entry.inlinetime, "cumulative_s": entry.totaltime})
        for child in entry.calls or ():
            all_edges.append((key, id(child.code), child))
    top_self = sorted(rows, key=lambda row: (-row["self_s"], row["id"]))[:TOP]
    top_cum = sorted(rows, key=lambda row: (-row["cumulative_s"], row["id"]))[:TOP]
    selected = {row["id"] for row in top_self + top_cum}
    # Pin both encode callers first. Remaining slots show direct key-owner
    # edges only. Shared deeper helpers' self remains combined across passes.
    root_code, measure, encode, parse = key_codes
    pinned = {(root_code, encode), (measure, encode)}
    key_edges = [edge for edge in all_edges if edge[0] in key_codes]
    eligible = [edge for edge in key_edges if edge[0] in ids and edge[1] in ids]
    eligible.sort(key=lambda edge: ((edge[0], edge[1]) not in pinned,
                                   -edge[2].totaltime, ids[edge[0]], ids[edge[1]]))
    edges = []
    for caller, callee, entry in eligible[:EDGE_LIMIT]:
        # Include bounded endpoint descriptors without promoting extra rows
        # into either top-N ranking.
        edges.append({"caller": rows[ids[caller]], "callee": rows[ids[callee]],
                      "calls": entry.callcount, "recursive_calls": entry.reccallcount,
                      "self_s": entry.inlinetime, "cumulative_s": entry.totaltime})
    total_self = sum(row["self_s"] for row in rows)
    shown_self = sum(rows[index]["self_s"] for index in selected)
    return {
        "functions": [row for row in rows if row["id"] in selected],
        "top_self_ids": [row["id"] for row in top_self],
        "top_cumulative_ids": [row["id"] for row in top_cum], "key_caller_edges": edges,
        "coverage": {"functions_total": len(rows), "functions_shown": len(selected),
                     "functions_omitted": len(rows) - len(selected),
                     "self_s_total": total_self, "self_s_shown": shown_self,
                     "self_s_omitted": total_self - shown_self,
                     "edges_total": len(all_edges), "key_edges_total": len(key_edges),
                     "key_edges_identity_unresolved": len(key_edges) - len(eligible),
                     "edges_shown": len(edges), "edges_omitted": len(all_edges) - len(edges)},
        "limits": {"top_per_ranking": TOP, "key_edges": EDGE_LIMIT, "report_bytes": REPORT_LIMIT},
        "attribution": "Direct encode caller edges separate measure and emit; deeper helper self is combined. Cumulative times overlap and must not be summed.",
        "edge_identity": "Raw code identity only. Unresolved edge identities, including any separately materialized builtin labels, are counted as omitted; labels are never merged.",
    }


def _attach(config, manifest_sha256, assembly, timing, encoder, elf):
    original = assembly._assemble_file
    original_report = timing.ModulePhaseTiming.report
    state = {"used": False, "owned": False, "profiler": None, "timing_id": None,
             "status": "NOT_CAPTURED", "diagnostic_errors": [], "disabled": False}
    directory = Path(config["directory"])
    base = {"schema": SCHEMA, "identity": config["identity"],
            "worker_manifest_sha256": manifest_sha256,
            "scope": "one real _assemble_file; profiled overhead, not baseline performance",
            "selected_worker_startup": "ordinary eager PCC import after singleton admission; perturbed",
            "observer_failure": "Partial enable/disable may leave profiling active beyond assembly; invalid captures publish no stats. Arbitrary same-name monitoring takeover is not guaranteed safe.",
            "strong_kill": "may leave claim.json without stats; no completion is inferred"}

    def note(value):
        try:
            if len(state["diagnostic_errors"]) < 8:
                state["diagnostic_errors"].append(value)
        except BaseException:
            pass

    def restore_assembly():
        try:
            if assembly._assemble_file is measured:
                assembly._assemble_file = original
        except BaseException:
            note("assembly_wrapper_restore_failed")

    def measured(*args, **kwargs):
        phase = kwargs.get("phase_timing", args[6] if len(args) > 6 else None)
        if (state["used"] or phase is None or phase.module_name != MODULE
                or phase.target != TARGET or phase.route != ROUTE):
            return original(*args, **kwargs)
        state["used"] = True
        state["timing_id"] = id(phase)
        try:
            _write(directory / "claim.json", dict(base, status="CLAIMED"))
        except BaseException:
            # Another attempt owns this build's one claim, or diagnostics are
            # unavailable. Neither changes ordinary compilation or retries.
            return original(*args, **kwargs)
        state["owned"] = True
        if _observer_conflict():
            state["status"] = "PROFILE_HOOK_CONFLICT"
            return original(*args, **kwargs)
        try:
            from cProfile import Profile
            profiler = Profile(subcalls=True, builtins=True)
        except BaseException:
            state["status"] = "PROFILE_SETUP_FAILED"
            return original(*args, **kwargs)
        state["profiler"] = profiler
        # Imports/audit callbacks may themselves install a profiler. Never
        # override a hook that appeared since the first conflict check.
        if _observer_conflict():
            state["profiler"] = None
            state["status"] = "PROFILE_HOOK_CONFLICT"
            return original(*args, **kwargs)
        try:
            profiler.enable()
        except BaseException:
            note("enable_failed")
        state["status"] = "ASSEMBLY_RAISED"
        try:
            result = original(*args, **kwargs)
            state["status"] = "ASSEMBLY_RETURNED"
            return result
        finally:
            # Only the official API, once. Do not reset legacy hooks, swap
            # monitoring callbacks, or free a slot as a recovery mechanism.
            # The public slot name cannot prove instance identity after an
            # arbitrary same-name takeover; that case is not guaranteed safe.
            try:
                if sys.getprofile() is not None:
                    note("foreign_legacy_hook_preserved")
                slot = sys.monitoring.get_tool(sys.monitoring.PROFILER_ID)
                if slot not in (None, "cProfile"):
                    note("foreign_monitoring_slot_preserved")
                else:
                    profiler.disable()
                    state["disabled"] = sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is None
                    if not state["disabled"]:
                        note("profile_state_not_restored")
            except BaseException:
                note("disable_failed_state_not_restored")
            if state["diagnostic_errors"] or not state["disabled"]:
                global _FAILED_PROFILER
                _FAILED_PROFILER = profiler
            # No report here: do not extend the assembly/ELF return lifetime
            # through serialization or add that work to the codegen stopwatch.
            restore_assembly()

    def report(phase, *args, **kwargs):
        try:
            return original_report(phase, *args, **kwargs)
        finally:
            if state["timing_id"] == id(phase):
                profiler = state.pop("profiler", None)
                try:
                    if timing.ModulePhaseTiming.report is report:
                        timing.ModulePhaseTiming.report = original_report
                except BaseException:
                    note("report_wrapper_restore_failed")
                restore_assembly()
                try:
                    if state["owned"]:
                        value = dict(base, status=state["status"],
                                     diagnostic_errors=state["diagnostic_errors"],
                                     capture_valid=profiler is not None and state["disabled"]
                                     and not state["diagnostic_errors"])
                        if value["capture_valid"]:
                            value.update(_summary(profiler.getstats(), Path(config["root"]),
                                                  _labels((assembly, encoder, elf)),
                                                  (id(original.__code__), id(assembly._measure_sections.__code__),
                                                   id(encoder.encode_instruction.__code__), id(assembly._parse_file.__code__))))
                        _write(directory / "result.json", value)
                except BaseException:
                    # An original compiler/report exception always has priority.
                    try:
                        _write(directory / "result.json", dict(base, status="PROFILE_REPORT_FAILED",
                                                               assembly_status=state["status"], capture_valid=False))
                    except BaseException:
                        pass
                finally:
                    if profiler is not None and state["disabled"]:
                        try:
                            profiler.clear()
                        except BaseException:
                            pass

    try:
        assembly._assemble_file = measured
        timing.ModulePhaseTiming.report = report
    except BaseException:
        # Roll back only this adapter's own partial installation.
        restore_assembly()
        try:
            if timing.ModulePhaseTiming.report is report:
                timing.ModulePhaseTiming.report = original_report
        except BaseException:
            pass
        raise


def install():
    """sitecustomize entry; no hooks, PCC imports or profiler for other jobs."""
    if (os.environ.get(FLAG) != "1" or not os.environ.get(CONFIG)
            or sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
            or sys.platform != "linux"
            or platform.machine().lower() not in ("x86_64", "amd64")):
        return
    argv = getattr(sys, "orig_argv", ())
    if (len(argv) != 5 or list(argv[:4]) != [sys.executable, "-m", "pcc",
                                             "--pcc-python-multi-codegen-worker"]):
        return
    config, manifest = None, None
    status = "SETUP_UNREADABLE"

    def publish_status():
        if manifest is not None:
            try:
                _write(Path(config["directory"]) / "install-status.json", {
                    "schema": SCHEMA, "status": status,
                    "scope": "first admitted worker installation; retries do not replace this record",
                    "identity": config["identity"], "worker_manifest_sha256": manifest,
                })
            except BaseException:
                pass

    try:
        with open(os.environ[CONFIG], encoding="ascii") as stream:
            raw = stream.read(REPORT_LIMIT + 1)
        if len(raw) > REPORT_LIMIT:
            return
        config = json.loads(raw)
        config.update(root=os.environ[SOURCE_ROOT], directory=str(Path(os.environ[CONFIG]).parent))
        manifest = _admit(list(argv), config["root"])
        if manifest is None:
            return
        status = "ADAPTER_IDENTITY_MISMATCH"
        if config["identity"]["adapter_sha256"] != _digest(__file__):
            return
        status = "PROFILE_HOOK_CONFLICT"
        if _observer_conflict():
            return
        status = "OWNER_IMPORT_FAILED"
        loaded = [importlib.import_module(name) for name in OWNERS]
        status = "OWNER_IDENTITY_MISMATCH"
        for module, name in zip(loaded, OWNERS):
            path = Path(config["root"]) / (name.replace(".", "/") + ".py")
            if (Path(module.__file__).resolve() != path
                    or _digest(path) != config["identity"]["owner_sources"][name.replace(".", "/") + ".py"]):
                return
        assembly, timing, encoder, elf = loaded
        status = "ATTACH_FAILED"
        _attach(config, manifest, assembly, timing, encoder, elf)
        status = "INSTALLED"
    except BaseException:
        # Startup diagnostics never replace the worker's ordinary import/error.
        # A bounded status for admitted workers distinguishes installation
        # failure from capture loss, without exporting exception text/paths.
        return
    finally:
        publish_status()

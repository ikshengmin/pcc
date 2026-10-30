#!/usr/bin/env python3
"""Run the actual TLA+ tools on FlowGCTransfer, not the Python mirror.

Requires an explicitly supplied, locally installed official tla2tools.jar.
Does not download dependencies, modify the specification, or execute PCC/GC.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile


@contextmanager
def run_lock(path):
    if path is None:
        yield
        return
    import fcntl

    with Path(path).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_process(command, *, cwd, timeout):
    started = time.perf_counter()
    process = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise RuntimeError("TLA+ tool exceeded its bounded timeout") from None
    return process.returncode, output, time.perf_counter() - started


def check_sany_result(code, output):
    # SANY versions can report semantic failure without a nonzero exit code.
    if (code != 0 or "Semantic processing of module FlowGCTransfer" not in output
            or re.search(r"(?im)^.*(?:fatal errors|parse errors|semantic errors|abort|error:)", output)):
        raise AssertionError("SANY did not accept FlowGCTransfer: " + output[-4000:])


def summarize_tlc(code, output, *, negative):
    violation = re.findall(r"Invariant ([A-Za-z_][\w]*) is violated", output)
    if negative:
        if code != 12 or violation != ["NoLostLiveObjects"]:
            raise AssertionError("negative control did not fail for the expected invariant: " + output[-4000:])
    elif code != 0 or "Model checking completed. No error has been found." not in output or violation:
        raise AssertionError("positive model check did not finish successfully: " + output[-4000:])
    counts = re.search(r"([\d,]+) states generated, ([\d,]+) distinct states found, ([\d,]+) states left on queue", output)
    depth = re.search(r"The depth of the complete state graph search is (\d+)", output)
    if counts is None or depth is None:
        raise AssertionError("TLC did not report a completed check/counterexample summary")
    generated, distinct, queued = (int(value.replace(",", "")) for value in counts.groups())
    if not negative and queued:
        raise AssertionError("positive model left unexplored states")
    result = {
        "exit_code": code, "states_generated": generated,
        "distinct_states": distinct, "states_left": queued,
        "depth": int(depth.group(1)), "violated_invariants": violation,
        "complete_exploration": not negative,
    }
    if negative:
        result["counterexample_actions"] = re.findall(r"State \d+: <([^ ]+)", output)
        if not result["counterexample_actions"] or "missed = {o1}" not in output:
            raise AssertionError("negative control lacks its missed-live-object witness")
    return result


def checked_config_bounds(positive, negative):
    """Accept the bounded experiment's explicit config grammar, not arbitrary TLC.

    Reject unsupported syntax or a changed boundary before starting Java.  In
    particular, the negative witness parser assumes the original o1/o2 domain.
    """
    def clean(text):
        return " ".join(line.split("\\*", 1)[0].strip() for line in text.splitlines()).strip()

    positive, negative = clean(positive), clean(negative)
    positive = " ".join(positive.split())
    negative = " ".join(negative.split())
    pattern = (
        r"CONSTANTS MaxSources = (?P<max_sources>[1-9][0-9]*) "
        r"ObjectIds = \{\s*(?P<object_ids>[A-Za-z_][A-Za-z_0-9]*"
        r"(?:\s*,\s*[A-Za-z_][A-Za-z_0-9]*)*)\s*\} "
        r"UseBarrier = TRUE SPECIFICATION Spec INVARIANTS "
        r"TypeOK OwnershipConserved PrefixVisible AllOwnedVisible ScanOrder "
        r"PublishedComplete NoLostLiveObjects"
    )
    match = re.fullmatch(pattern, positive)
    if match is None:
        raise AssertionError("unsupported positive config: expected the explicit FlowGCTransfer boundary")
    if positive.replace("UseBarrier = TRUE", "UseBarrier = FALSE") != negative:
        raise AssertionError("negative config must differ only by UseBarrier TRUE -> FALSE")
    max_sources = int(match.group("max_sources"))
    object_ids = [value.strip() for value in match.group("object_ids").split(",")]
    if max_sources != 3 or object_ids != ["o1", "o2"]:
        raise AssertionError("this bounded experiment requires MaxSources = 3 and ObjectIds = {o1, o2}")
    return {"max_sources": max_sources, "object_ids": object_ids}


def experiment(repo, jar, *, java="java", timeout=60, lock=None):
    repo, jar = Path(repo).resolve(), Path(jar).resolve()
    if not jar.is_file():
        raise FileNotFoundError("provide an existing official tla2tools.jar: " + str(jar))
    specs = repo / "specs/tla"
    positive = (specs / "FlowGCTransfer.cfg").read_text()
    negative = (specs / "FlowGCTransferNoBarrier.cfg").read_text()
    bounds = checked_config_bounds(positive, negative)
    with zipfile.ZipFile(jar) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF").decode()
    identity = dict(re.findall(r"(?m)^(Implementation-Version|X-Git-Tag|X-Git-Revision): (.+?)\r?$", manifest))
    result = {
        "boundary": "SANY semantic checking and finite TLC exploration; not PCC/GC execution",
        "model": "FlowGCTransfer", **bounds,
        "tool": identity, "workers": 1, "heap_limit_mib": 256,
    }
    with run_lock(lock), tempfile.TemporaryDirectory(prefix="pcc-flow-tlc-") as temporary:
        work = Path(temporary)
        for name in ("FlowGCTransfer.tla", "FlowGCTransfer.cfg", "FlowGCTransferNoBarrier.cfg"):
            shutil.copyfile(specs / name, work / name)
        prefix = [java, "-Xmx256m", "-XX:+UseSerialGC", "-cp", str(jar)]
        code, output, elapsed = run_process(prefix + ["tla2sany.SANY", "FlowGCTransfer.tla"], cwd=work, timeout=timeout)
        check_sany_result(code, output)
        result["sany"] = {"exit_code": code, "accepted": True, "wall_seconds": elapsed}
        for name, is_negative in (("positive", False), ("barrier_removed", True)):
            config = "FlowGCTransferNoBarrier.cfg" if is_negative else "FlowGCTransfer.cfg"
            command = prefix + ["tlc2.TLC", "-workers", "1", "-seed", "1", "-fp", "0", "-config", config,
                                "-metadir", str(work / (name + "-states")), "FlowGCTransfer.tla"]
            code, output, elapsed = run_process(command, cwd=work, timeout=timeout)
            result[name] = summarize_tlc(code, output, negative=is_negative)
            result[name]["wall_seconds_including_jvm_startup"] = elapsed
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    print(json.dumps(experiment(args.repo, args.jar, java=args.java, timeout=args.timeout, lock=args.lock), indent=2))


if __name__ == "__main__":
    main()

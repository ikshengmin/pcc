"""Hash-only failed-canary identity. This is never a native-input admission.

The workflow lease and immutable Git tree are captured when run_chain starts.
The clean source is the producer's existing source snapshot. Image/runtime
hashes originate before the failing canary; failure handling rechecks their
bytes, source, target and provenance before publishing only these identities.
No commands, paths, exceptions, environment, receipts or payloads are copied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from scripts.ci_stage1_bundle import (
    MAX_FILE, MAX_MANIFEST, PLATFORMS, RUNTIME, regular_file, require,
    valid_digest, verify_native_header, workflow_lease,
)

SCHEMA = "pcc.failed-stage-identity.v2"
ASSOCIATION_FIELD = "failure_identity_invocation_sha256"
FILENAME = "failure-identity.json"
SOURCE_SCOPE = "compiler-runtime-headers-build-harness-v1"
PLATFORM_IDS = frozenset(PLATFORMS) - {"macos-arm64"}
PHASES = frozenset({"canary_compile", "canary_run"})


def _source(source):
    require(source.get("clean_commit") is True
            and valid_digest(source.get("commit"), 40)
            and valid_digest(source.get("source_sha256"))
            and source.get("source_scope") == SOURCE_SCOPE
            and type(source.get("source_file_count")) is int
            and 0 < source["source_file_count"] < 1000000, "invalid source identity")
    return {key: source[key] for key in (
        "commit", "source_sha256", "clean_commit", "source_scope", "source_file_count")}


def _lease(environ=None):
    lease = workflow_lease(environ)
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", lease["repository"])
            and all(part not in (".", "..") for part in lease["repository"].split("/")),
            "invalid repository identity")
    require(lease["run_id"] < 2**63 and lease["run_attempt"] < 2**31,
            "invalid workflow identity")
    return lease


def _tree(root, commit):
    # A validated full SHA is the only revision argument, never receipt text.
    value = subprocess.check_output(
        ["git", "rev-parse", commit + "^{tree}"], cwd=root, text=True, timeout=10,
        stderr=subprocess.DEVNULL,
    ).strip()
    require(valid_digest(value, 40), "invalid source tree identity")
    return value


def _new_invocation():
    # An occurrence identifier, not an authentication credential. Retain only
    # its digest, never the random input, in the existing failure receipt.
    material = os.urandom(32)
    require(type(material) is bytes and len(material) == 32, "invalid random input")
    return hashlib.sha256(material).hexdigest()


def capture(root, source, target, environ=None):
    """Optional diagnostic context, captured before any stage/canary execution."""
    try:
        lease = _lease(environ)
        source = _source(source)
        require(source["commit"] == lease["sha"], "source event differs")
        platform_id = next(name for name in PLATFORM_IDS if PLATFORMS[name]["target"] == target)
        invocation = _new_invocation()
        require(valid_digest(invocation), "invalid invocation identity")
        return {"producer": lease, "source": dict(source, tree_sha1=_tree(root, source["commit"])),
                "platform": platform_id, "target": target, ASSOCIATION_FIELD: invocation}
    except Exception:
        # Missing CI identity, dirty source, denied reads and unsupported hosts
        # disable only this optional output. No recovery or alternate reads.
        return None


def _checked_hash(path, root, expected):
    require(valid_digest(expected), "invalid expected hash")
    before = regular_file(path, root)
    require(0 < before.st_size <= MAX_FILE, "invalid input size")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        require(stat.S_ISREG(opened.st_mode) and identity(opened) == identity(before),
                "input identity changed")
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
        require(identity(os.fstat(stream.fileno())) == identity(opened)
                and identity(regular_file(path, root)) == identity(before), "input changed")
    require(actual == expected, "input hash differs")
    return actual


def _verify_runtime(root, runtime, target):
    # Canonical source/member/configuration verifier, with no provisioning.
    from scripts.ci_stage1_artifact_export import _admit_runtime
    _admit_runtime(root, runtime, target)


def _termination(error, platform_id):
    require(type(error) is subprocess.CalledProcessError, "no terminal child status")
    code = error.returncode
    require(type(code) is int and code != 0, "invalid child status")
    if platform_id == "windows-x86_64":
        require(-(2**31) <= code < 2**32, "invalid Windows child status")
        return {"returncode": code, "exit_code": code & 0xffffffff, "signal": None}
    require(-64 <= code <= 255, "invalid POSIX child status")
    return {"returncode": code, "exit_code": code if code > 0 else None,
            "signal": -code if code < 0 else None}


def validate_identity(value):
    """Schema only, never proof of a current invocation or successful admission."""
    require(type(value) is dict and set(value) == {
        "schema", "status", "qualified", "admissible", "producer", "source",
        "platform", "target", "stage", "gc", "failure", "hashes"}, "identity fields differ")
    require(value["schema"] == SCHEMA and value["status"] == "FAIL"
            and value["qualified"] is False and value["admissible"] is False,
            "identity is not a diagnostic failure")
    platform_id = value["platform"]
    require(platform_id in PLATFORM_IDS and value["target"] == PLATFORMS[platform_id]["target"],
            "identity target differs")
    require(type(value["stage"]) is int and value["stage"] == 1
            and type(value["gc"]) is int and value["gc"] in range(5), "identity stage differs")
    producer = value["producer"]
    require(type(producer) is dict and set(producer) == {
        "repository", "workflow", "run_id", "run_attempt", "sha"}, "producer fields differ")
    require(type(producer["run_id"]) is int and type(producer["run_attempt"]) is int,
            "producer numbers differ")
    env = {"GITHUB_REPOSITORY": producer["repository"],
           "GITHUB_WORKFLOW_REF": producer["repository"] + "/" + producer["workflow"] + "@identity",
           "GITHUB_RUN_ID": str(producer["run_id"]), "GITHUB_RUN_ATTEMPT": str(producer["run_attempt"]),
           "GITHUB_SHA": producer["sha"]}
    require(_lease(env) == producer, "producer identity differs")
    source = value["source"]
    require(type(source) is dict and set(source) == {
        "commit", "tree_sha1", "source_sha256", "clean_commit", "source_scope", "source_file_count"},
        "source fields differ")
    require(_source(source)["commit"] == producer["sha"] and valid_digest(source["tree_sha1"], 40),
            "source tree differs")
    failure = value["failure"]
    require(type(failure) is dict and set(failure) == {"phase", "returncode", "exit_code", "signal"}
            and failure["phase"] in PHASES, "failure fields differ")
    termination = _termination(subprocess.CalledProcessError(failure["returncode"], []), platform_id)
    require(all(failure[key] == item and type(failure[key]) is type(item)
                for key, item in termination.items()), "termination fields differ")
    require(type(value["hashes"]) is dict and set(value["hashes"]) == {
        "compiler_sha256", "runtime_sha256", "runtime_provenance_sha256", "capi_inventory_sha256",
        "failure_receipt_sha256"}
            and all(valid_digest(item) for item in value["hashes"].values()), "hash fields differ")
    return value


def _read_json_bytes(path, root, limit):
    before = regular_file(path, root)
    require(0 < before.st_size <= limit, "invalid receipt size")
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and identity(opened) == identity(before),
                "receipt identity changed")
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and len(data) <= limit
                and identity(os.fstat(stream.fileno())) == identity(opened)
                and identity(regular_file(path, root)) == identity(before), "receipt changed")
    return data


def _failure_binding(root, out_dir, expected_invocation):
    # The producer has already completed its one original failure.json write.
    # Its contents are used locally for binding, never copied into the sidecar.
    require(valid_digest(expected_invocation), "missing expected invocation")
    data = _read_json_bytes(out_dir / "failure.json", root, MAX_MANIFEST)
    receipt = json.loads(data)
    observed = receipt.get(ASSOCIATION_FIELD)
    require(valid_digest(observed), "missing current failure association")
    require(observed == expected_invocation, "failure invocation differs")
    return hashlib.sha256(data).hexdigest(), observed


def verify_current_identity(root, out_dir, *, expected_invocation, environ=None):
    """Verify against this invocation's independently retained context digest.

    expected_invocation is required, with no fallback. It must come from the
    caller's independently known invocation context, never these two files.
    Downloaders without that context can inspect a recorded identity/schema,
    but cannot establish or claim current-invocation authentication with it.
    A same-lease old pair can remain after mandatory cleanup fails; its valid
    stored digest is not an independent expectation for the new invocation.
    Missing/null identity, stale files, mutation or denied reads reject the
    diagnostic. No failure contents are returned or re-exported.
    This is not successful Stage1 admission and does not inspect binary inputs.
    """
    try:
        require(valid_digest(expected_invocation), "missing expected invocation")
        root, out_dir = Path(root), Path(out_dir)
        require(out_dir in {root / "build/platform-qualification" / name
                            for name in ("shared", "gc0", "gc1", "gc2", "gc3", "gc4")},
                "unsupported failure directory")
        path = out_dir / FILENAME
        data = _read_json_bytes(path, root, 4096)
        value = validate_identity(json.loads(data))
        require(value["producer"] == _lease(environ), "current workflow differs")
        require(out_dir.name == "shared" or out_dir.name == "gc" + str(value["gc"]),
                "current collector differs")
        failure_hash, _association = _failure_binding(root, out_dir, expected_invocation)
        require(value["hashes"]["failure_receipt_sha256"] == failure_hash, "current failure differs")
        _checked_hash(path, root, hashlib.sha256(data).hexdigest())
        require(_failure_binding(root, out_dir, expected_invocation)[0] == failure_hash,
                "current failure changed")
        return value
    except Exception:
        # Do not leak arbitrary receipt text or filesystem paths to a caller.
        raise ValueError("current failure identity is unavailable") from None


def record(context, *, root, out_dir, source, current_source, target, gc, incomplete,
           runtime_identity, phase, command, error, environ=None):
    """Write one minimal sidecar, or nothing; never alter the original failure.

    Only a completed Stage1's own nonzero canary child termination is covered.
    Timeouts, cancellation, partial images and missing evidence remain unbound.
    Callers retain the original exception regardless of this optional result.
    """
    if context is None:
        return False
    try:
        root, out_dir = Path(root), Path(out_dir)
        require(context["producer"] == _lease(environ)
                and valid_digest(context.get(ASSOCIATION_FIELD)), "workflow or invocation changed")
        require(context["source"] == dict(_source(source), tree_sha1=_tree(root, source["commit"]))
                and _source(current_source()) == _source(source), "source changed")
        require(context["target"] == target and phase in PHASES, "target or phase differs")
        require(type(gc) is str and gc in ("0", "1", "2", "3", "4"), "invalid GC")
        require(out_dir in {root / "build/platform-qualification" / name
                            for name in ("shared", "gc" + gc)}, "unsupported failure directory")
        require(type(incomplete) is dict and type(incomplete.get("stage")) is int
                and incomplete["stage"] == 1
                and incomplete.get("canary_status") == "pending" and "canary" not in incomplete,
                "no completed unvalidated Stage1")
        platform_id = context["platform"]
        suffix = ".exe" if platform_id == "windows-x86_64" else ""
        compiler = (root / "build/platform-qualification/shared" if incomplete.get("reused") is True
                    else out_dir) / ("pcc1" + suffix)
        require(incomplete["image"]["path"] == str(compiler), "compiler location differs")
        runtime = root / RUNTIME
        require(runtime_identity["path"] == str(runtime), "runtime location differs")
        expected_command = ([str(compiler), str(out_dir / "canary.py"), "-o",
                             str(out_dir / ("canary1" + suffix)), "--backend", "self"]
                            if phase == "canary_compile" else [str(out_dir / ("canary1" + suffix))])
        require(command == expected_command and error.cmd == command, "failed child differs")
        termination = _termination(error, platform_id)
        inputs = [(compiler, incomplete["sha256"], "compiler_sha256")]
        inputs.extend((Path(str(runtime) + ending), runtime_identity[key], output)
                      for ending, key, output in (
                          ("", "sha256", "runtime_sha256"),
                          (".provenance.json", "provenance_sha256", "runtime_provenance_sha256"),
                          (".capi_syms", "capi_inventory_sha256", "capi_inventory_sha256")))
        failure_hash, _association = _failure_binding(root, out_dir, context[ASSOCIATION_FIELD])
        hashes = {key: _checked_hash(path, root, expected) for path, expected, key in inputs}
        hashes["failure_receipt_sha256"] = failure_hash
        require(type(incomplete["image"].get("file_size")) is int
                and incomplete["image"]["file_size"] == compiler.stat().st_size, "image size differs")
        verify_native_header(compiler, platform_id)
        _verify_runtime(root, runtime, target)
        require(all(_checked_hash(path, root, expected) == hashes[key] for path, expected, key in inputs)
                and _source(current_source()) == _source(source)
                and context["producer"] == _lease(environ)
                and _failure_binding(root, out_dir, context[ASSOCIATION_FIELD])[0] == failure_hash,
                "inputs changed during inspection")
        value = validate_identity({"schema": SCHEMA, "status": "FAIL", "qualified": False,
            "admissible": False,
            **{key: context[key] for key in ("producer", "source", "platform", "target")},
            "stage": 1, "gc": int(gc),
            "failure": {"phase": phase, **termination}, "hashes": hashes})
        data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")
        require(len(data) <= 4096, "identity size differs")
        # Check every output ancestor without resolving a symlink away. The
        # filename and content are fixed; no user-supplied output path is used.
        for directory in (out_dir, *out_dir.parents):
            require(not directory.is_symlink(), "unsafe output ancestor")
            if directory == root:
                break
        destination = out_dir / FILENAME
        # mkstemp is exclusive and mode 0600. A failed write/close can leave
        # only a .tmp file, outside the existing JSON/log artifact globs.
        fd, temporary = tempfile.mkstemp(prefix=".failure-identity-", suffix=".tmp", dir=out_dir)
        try:
            stream = os.fdopen(fd, "wb")
            fd = None  # The stream owns the descriptor, including close failure.
            with stream:
                stream.write(data)
            # POSIX link and Windows CreateHardLink publish a closed file as
            # one directory operation and reject an existing destination.
            # Unsupported filesystems fail closed; there is no overwrite or
            # copy fallback. Source and destination are on the same filesystem.
            os.link(temporary, destination, follow_symlinks=False)
            return True
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                Path(temporary).unlink()
            except OSError:
                # A denied cleanup is not retried by another route. Any
                # residue remains non-artifact .tmp, never partial final JSON.
                pass
    except Exception:
        return False

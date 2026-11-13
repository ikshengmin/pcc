"""Read-only exact-source qualification for the opt-in, queued compiler A/B.

Selectors are not evidence. Every call rechecks GitHub's run-attempt, job and
immutable artifact metadata. Artifact bytes are admitted only by the provider's
SHA256 digest. Existing Stage1 exports supply runtime bytes, never performance
compiler receipts. Fresh strict compiler builds belong to run_pcc_ci_ab.py.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "ikshengmin/pcc"
WORKFLOW = ".github/workflows/pcc1-package-parity.yml"
SOURCE = "pcc/frontends/python/pipeline_import_scan.py"
MASTER_REF = "refs/heads/master"
SCANNER_CORPUS = "benchmarks/ci_ab/import_scan/manifest.json"
CANDIDATE_REF = re.compile(
    r"refs/heads/ci-ab/scanner/b(?P<baseline>[0-9a-f]{40})"
    r"-r(?P<run>[1-9][0-9]{0,19})-a(?P<attempt>[1-9][0-9]{0,19})/c(?P<candidate>[0-9a-f]{40})"
)
# Strong references prevent ID reuse. Only live verifier returns are admitted;
# a serialized copy, even one labelled PASS, is never an authorization input.
_LIVE_PROOFS = {}
PLATFORMS = ("macos-arm64", "linux-x86_64", "linux-aarch64", "windows-x86_64")
DOMAINS = {"cpp", "rust", "java", "go", "shared", "ci-contracts"}
LANES = {"ordinary", "integration", "reference"}
ORIGINAL_JOBS = (
    "validation-plan", "source-validation", "shared-source-validation",
    "source-platform-validation", "source-platform-coverage", "numpy-source-frontier",
    "orjson-source-frontier", "ci-plan", "macos-pcc0-regressions",
    "macos-pcc0-float-doc-regressions", "macos-pcc0-indexed-split",
    "worker-prior-preflight", "windows-rss-publication", "other-platform-native-wheel",
    "pcc1-package-parity", "pytest-markers", "cpp17",
)
BOOTSTRAP_NODES = {
    f"tests/python/gc/test_pcc_bootstrap_full_gc{gc}.py::test_full_three_stage_bootstrap_self_gc{gc}"
    for gc in range(5)
}
MAX_JSON = 32 * 1024 * 1024
MAX_ARCHIVE = 4 * 1024**3
MAX_SOURCE = 2 * 1024**3
ROOT = Path(__file__).resolve().parents[1]
_DEADLINE_MONOTONIC = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value, length=64):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def decode(data):
    require(len(data) <= MAX_JSON, "JSON exceeds bounded admission")
    return json.loads(data, object_pairs_hook=unique)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def relative(name):
    require(isinstance(name, str) and name and "\\" not in name and "\x00" not in name,
            "invalid relative file name")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in (".", "..", "") for part in name.split("/")),
            "path escapes immutable inputs")
    return path


def remaining():
    raw = os.environ.get("PCC_CI_AB_DEADLINE_EPOCH", "")
    try:
        deadline = float(raw)
    except ValueError as error:
        raise ValueError("missing absolute CI campaign deadline") from error
    value = deadline - time.time()
    if _DEADLINE_MONOTONIC is not None:
        value = min(value, _DEADLINE_MONOTONIC - time.monotonic())
    require(math.isfinite(deadline) and 0 < value <= 12000,
            "campaign deadline exhausted or exceeds original 200-minute envelope")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class GitHub:
    """Fixed repository/API, GET only; never forward the token to blob storage."""
    def __init__(self, cache):
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "foreign repository")
        self.token = os.environ.get("GH_TOKEN", "")
        require(self.token, "read-only GitHub credential is unavailable")
        self.cache = Path(cache)
        require(not self.cache.is_symlink(), "artifact cache is a symlink")
        self.cache.mkdir(parents=True, exist_ok=True)

    def request(self, path):
        require(path.startswith("/") and not path.startswith("//") and ".." not in path,
                "invalid fixed-repository API path")
        return urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                      headers={"Authorization": "Bearer " + self.token,
                                               "Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28"})

    def get(self, path):
        # API responses must never redirect authentication to another endpoint.
        with urllib.request.build_opener(NoRedirect).open(self.request(path), timeout=min(20, remaining())) as response:
            return decode(response.read(MAX_JSON + 1))

    def pages(self, path, key):
        rows = []
        count = None
        for page in range(1, 11):
            data = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            require(type(data.get("total_count")) is int and data["total_count"] >= 0, "missing page total")
            count = data["total_count"] if count is None else count
            require(data["total_count"] == count and isinstance(data.get(key), list), "pagination changed")
            rows.extend(data[key])
            require(len(rows) <= count, "duplicate or inconsistent API pages")
            if len(rows) == count:
                return rows
            require(data[key], "incomplete API pagination")
        raise ValueError("API pagination exceeds bounded admission")

    def artifact(self, meta):
        identity = meta.get("digest", "")
        require(isinstance(identity, str) and identity.startswith("sha256:") and sha(identity[7:]),
                "provider artifact digest unavailable")
        require(type(meta.get("id")) is int and meta["id"] > 0
                and type(meta.get("size_in_bytes")) is int
                and 0 < meta["size_in_bytes"] <= MAX_ARCHIVE and meta.get("expired") is False,
                "expired, oversized or malformed artifact")
        target = self.cache / (identity[7:] + ".zip")
        if target.exists():
            require(not target.is_symlink() and digest(target) == identity[7:], "cached artifact bytes changed")
            return Archive(target)
        request = self.request(f"/actions/artifacts/{meta['id']}/zip")
        try:
            response = urllib.request.build_opener(NoRedirect).open(request, timeout=min(20, remaining()))
        except urllib.error.HTTPError as error:
            require(error.code in (301, 302, 303, 307, 308), "artifact download was rejected")
            location = error.headers.get("Location", "")
            parsed = urllib.parse.urlsplit(location)
            require(parsed.scheme == "https" and not parsed.username and not parsed.password
                    and parsed.port in (None, 443) and parsed.hostname is not None
                    and (parsed.hostname.endswith(".blob.core.windows.net")
                         or parsed.hostname.endswith(".actions.githubusercontent.com")),
                    "artifact redirect is not GitHub artifact storage")
            # A fresh request has no Authorization header. Further redirects fail.
            response = urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(location), timeout=min(20, remaining()))
        temporary = target.with_suffix(".partial")
        require(not temporary.exists(), "refuse incomplete previous artifact download")
        with response, temporary.open("xb") as output:
            length = 0
            while True:
                remaining()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                require(length <= MAX_ARCHIVE, "artifact download exceeds bound")
                output.write(chunk)
        require(digest(temporary) == identity[7:], "artifact differs from provider digest")
        temporary.rename(target)
        target.chmod(0o444)
        return Archive(target)


class Archive:
    """Read bounded regular ZIP members without unpacking arbitrary paths."""
    def __init__(self, path):
        self.path = Path(path)
        self.rows = {}
        with zipfile.ZipFile(path) as archive:
            total = 0
            for row in archive.infolist():
                if row.is_dir():
                    continue
                relative(row.filename)
                require(row.filename not in self.rows and not row.flag_bits & 1,
                        "duplicate or encrypted artifact member")
                mode = row.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG), "nonregular artifact member")
                require(0 <= row.file_size <= MAX_ARCHIVE, "oversized artifact member")
                total += row.file_size
                require(total <= MAX_ARCHIVE, "artifact expanded size exceeds bound")
                self.rows[row.filename] = row

    def name(self, suffix):
        if suffix in self.rows:
            return suffix
        matches = [name for name in self.rows if name == suffix or name.endswith("/" + suffix)]
        require(len(matches) == 1, "missing or ambiguous original artifact file: " + suffix)
        return matches[0]

    def bytes(self, suffix):
        name = self.name(suffix)
        require(self.rows[name].file_size <= MAX_JSON, "receipt exceeds bounded admission")
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(name)

    def json(self, suffix):
        return decode(self.bytes(suffix))


def source_snapshot(files):
    """Match bootstrap_platform._source_snapshot without importing the project."""
    selected = sorted(name for name in files if name.split("/", 1)[0] in {
        "pcc", "scripts", "src", "include", "utils", "hatch_build.py",
        "pyproject.toml", "uv.lock", ".python-version",
    })
    result = hashlib.sha256()
    for name in selected:
        encoded = name.encode("utf-8")
        result.update(len(encoded).to_bytes(8, "big"))
        result.update(encoded)
        result.update(b"file\0")
        result.update(bytes.fromhex(files[name]["sha256"]))
    return result.hexdigest()


def tree_files(root, *, sealed=True):
    root = Path(root).resolve(strict=True)
    rows = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        require(not path.is_symlink(), "source tree contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode) and (not sealed or not info.st_mode & 0o222),
                "source tree is not sealed regular files")
        name = path.relative_to(root).as_posix()
        relative(name)
        require(".git" not in Path(name).parts and "__pycache__" not in Path(name).parts,
                "source tree contains mutable repository/cache state")
        rows[name] = dict(sha256=digest(path), size_bytes=info.st_size, executable=bool(info.st_mode & 0o111))
    require(rows and sum(row["size_bytes"] for row in rows.values()) <= MAX_SOURCE, "empty/oversized source tree")
    return rows


def tree_digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bind_tree(api, source, files, root):
    require(sha(source, 40), "source is not a full immutable commit")
    commit = api.get("/git/commits/" + source)
    require(commit.get("sha") == source and sha(commit.get("tree", {}).get("sha"), 40), "commit identity differs")
    tree = api.get("/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    require(tree.get("truncated") is False and tree.get("sha") == commit["tree"]["sha"], "incomplete provider source tree")
    rows = {}
    for row in tree["tree"]:
        relative(row["path"])
        if row["type"] == "tree":
            continue
        require(row["type"] == "blob" and row["mode"] in ("100644", "100755")
                and row["path"] not in rows, "source has symlink, submodule or duplicate path")
        rows[row["path"]] = row
    require(set(rows) == set(files), "snapshot is not the complete exact provider source")
    for name, row in rows.items():
        path = Path(root) / name
        data = path.read_bytes()
        require(len(data) == row["size"] == files[name]["size_bytes"]
                and hashlib.sha256(data).hexdigest() == files[name]["sha256"]
                and (row["mode"] == "100755") == files[name]["executable"]
                and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == row["sha"],
                "provider/source bytes or mode differs: " + name)
    return commit["tree"]["sha"]


def lease(arm):
    return dict(repository=REPOSITORY, workflow=WORKFLOW, run_id=arm["run_id"],
                run_attempt=arm["run_attempt"], sha=arm["sha"])


def validate_run(run, arm, *, candidate, context):
    require(type(arm.get("run_id")) is int and arm["run_id"] > 0
            and type(arm.get("run_attempt")) is int and arm["run_attempt"] > 0
            and sha(arm.get("sha"), 40), "invalid immutable run selector")
    require(type(run.get("id")) is int and type(run.get("run_attempt")) is int
            and type(run.get("workflow_id")) is int
            and run.get("id") == arm["run_id"] and run.get("run_attempt") == arm["run_attempt"]
            and run.get("head_sha") == arm["sha"] and run.get("path") == WORKFLOW
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("head_branch") == (context["candidate_ref"][len("refs/heads/"):] if candidate else "master")
            and run.get("workflow_id") == context["workflow"]["id"],
            "provider run/source/repository/attempt/workflow differs")
    if candidate:
        require(run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress"
                and run.get("conclusion") is None, "candidate is not the active selected dispatch")
    else:
        require(run.get("event") in ("push", "workflow_dispatch") and run.get("status") == "completed"
                and run.get("conclusion") == "success", "baseline qualification was not successful")


def expected_jobs(plan):
    require(plan.get("legacy") is True and plan.get("full_chain") is False
            and set(plan.get("domains", [])) == DOMAINS, "plan did not retain all original qualifications")
    matrix = plan.get("matrix", {}).get("include", [])
    require({(row["domain"], row["lane"]) for row in matrix} == {(d, lane) for d in DOMAINS for lane in LANES}
            and len(matrix) == len(DOMAINS) * len(LANES), "whole source plan is incomplete")
    jobs = {
        "Select exact-source validation obligations", "Reconcile every planned source-platform obligation",
        "NumPy C/C++ host-PCC source frontier (not native pcc1)",
        "orjson Rust/C host-PCC source frontier (not native pcc1)",
        "Validate one fixed-point owner and complete pytest selection",
        "macOS arm64 focused pcc0 native regressions", "macOS arm64 pcc0 float and class-doc regressions",
        "macOS arm64 pcc0 indexed-stage bridge gate", "Windows RSS publication with a held reader",
        "macOS arm64 native pcc1 and installed wheel",
        "Linux x86_64 owned ELF and G++ reference", "macOS arm64 owned Mach-O and Clang++ reference",
    }
    jobs.update(f"Source / {row['domain']} / {row['lane']}" for row in matrix)
    jobs.update(f"Source / {row['domain']} / {row['lane']} / {row['platform']}"
                for row in plan["matrix_platform"]["include"])
    jobs.update("Worker prior and changed-module gate / " + platform for platform in PLATFORMS)
    jobs.update("Native wheel / " + platform for platform in PLATFORMS if platform != "macos-arm64")
    jobs.update(f"Complete pytest / {platform} / {marker}"
                for platform in PLATFORMS for marker in ("integration", "not integration"))
    return jobs


def validate_jobs(jobs, names, arm):
    by_name = {}
    for job in jobs:
        if job.get("name") not in names:
            continue
        require(job["name"] not in by_name, "duplicate original job owner")
        require(job.get("run_id") == arm["run_id"] and job.get("run_attempt") == arm["run_attempt"]
                and job.get("head_sha") == arm["sha"] and job.get("status") == "completed"
                and job.get("conclusion") == "success", "original job did not succeed on this exact attempt")
        by_name[job["name"]] = job
    require(set(by_name) == names, "missing original correctness/native/fixed-point owner")
    return sorted(job["id"] for job in by_name.values())


def validate_marker(archive, arm, platform, marker, partition, source_hash):
    receipt_name = archive.name("receipt.json")
    prefix = receipt_name[:-len("receipt.json")]
    receipt = archive.json(receipt_name)
    def raw(name):
        return archive.bytes(prefix + "pytest/" + name)
    def parsed(name):
        return decode(raw(name))
    require(receipt.get("status") == "PASS" and receipt.get("platform") == platform
            and receipt.get("marker") == marker and receipt.get("partition") == partition
            and receipt.get("producer") == lease(arm), "marker producer or terminal status differs")
    source = receipt.get("source", {})
    require(source.get("commit") == arm["sha"] and source.get("clean_commit") is True
            and source.get("source_sha256") == source_hash, "marker actual source differs")
    manifest = receipt.get("bundle", {})
    require(manifest.get("producer") == lease(arm) and manifest.get("source", {}).get("commit") == arm["sha"]
            and manifest.get("source", {}).get("source_sha256") == source_hash
            and manifest.get("source", {}).get("clean_commit") is True,
            "marker input bundle source/run differs")
    expected_configuration = dict(marker=marker, workers=6, dist="loadgroup")
    if partition != "all":
        expected_configuration["partition"] = partition
    require(parsed("configuration.json") == expected_configuration, "original six-worker policy differs")
    inventories = [parsed(f"inventory-gw{number}.json") for number in range(6)]
    first = inventories[0]
    require(all(row == first for row in inventories) and first.get("marker") == marker
            and first.get("partition", "all") == partition and not first.get("gated_out")
            and not first.get("unexpected"), "incomplete or inconsistent inventory")
    expected, selected = first["expected"], first["selected"]
    require(expected and len(expected) == len(set(expected)) and len(selected) == len(set(selected))
            and set(expected) == set(selected), "marker inventory omitted tests")
    events = [decode(line) for line in raw("live.jsonl").splitlines() if line]
    starts = [row for row in events if row.get("event") == "start"]
    require(len(starts) == 1 and starts[0].get("markexpr") == marker
            and all(not starts[0].get(key) for key in (
                "keyword", "ignore", "ignore_glob", "deselect", "collect_only", "override_ini",
                "noconftest", "confcutdir", "lf", "stepwise", "stepwise_skip"))
            and events[-1].get("event") == "finish" and events[-1].get("exitstatus") == 0,
            "pytest was narrowed or did not finish")
    reports = {}
    for row in events:
        if row.get("event") == "report":
            key = (row["nodeid"], row["when"])
            require(key not in reports and row.get("outcome") == "passed" and "wasxfail" not in row,
                    "duplicate, failed, skipped or xfailed original test")
            reports[key] = row
    require(set(reports) == {(node, phase) for node in selected for phase in ("setup", "call", "teardown")},
            "not all original selected nodes actually executed")
    if partition == "bootstrap":
        require(len(selected) == 5 and {node.split("@", 1)[0] for node in selected} == BOOTSTRAP_NODES,
                "not all original five-GC Stage1-to-Stage3 owners executed")
    guard_bytes = raw("result.json")
    require(hashlib.sha256(guard_bytes).hexdigest() == receipt.get("guard_result_sha256"),
            "guard receipt hash differs")
    guard = decode(guard_bytes)
    require(guard.get("status") == "COMPLETE" and guard.get("returncode") == 0,
            "original marker guard did not pass")
    timeout = receipt.get("timeout_s")
    limit = 2400 if marker == "not integration" else 1800
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= limit
            and (partition == "remainder" or timeout == limit), "original marker time envelope changed")
    timeout_key = "requested_timeout" if platform == "windows-x86_64" else "timeout_s"
    require(guard.get(timeout_key) == timeout, "marker guard timeout differs")
    if platform == "windows-x86_64":
        require(guard.get("requested_rss_limit") == 17179869184
                and type(guard.get("peak_tree_rss")) is int and 0 <= guard["peak_tree_rss"] <= 17179869184,
                "original Windows memory guard differs")
    else:
        cap = guard.get("max_tree_rss_bytes")
        require(type(cap) is int and (2147483648 <= cap <= 4294967296 if platform == "macos-arm64"
                                     else cap == 17179869184)
                and type(guard.get("peak_tree_rss_bytes")) is int
                and 0 <= guard["peak_tree_rss_bytes"] <= cap
                and not guard.get("post_exit_cleanup_pids"), "original process-tree memory/cleanup guard differs")
        if platform == "macos-arm64":
            selection = guard.get("memory_budget_selection", {})
            require(guard.get("darwin_preflight_reserve_bytes") == 536870912
                    and selection.get("selection_kind") == "automatic"
                    and selection.get("default_ceiling_bytes") == 4294967296
                    and selection.get("minimum_tree_rss_bytes") == 2147483648
                    and selection.get("configured_host_memory_reserve_bytes") == 536870912
                    and selection.get("max_tree_rss_bytes") == cap,
                    "original dynamic 2-4 GiB memory admission differs")
    return receipt, first


def verify_arm(api, arm, files, root, *, candidate, context):
    run = api.get(f"/actions/runs/{arm['run_id']}/attempts/{arm['run_attempt']}")
    validate_run(run, arm, candidate=candidate, context=context)
    tree = bind_tree(api, arm["sha"], files, root)
    metadata = api.pages(f"/actions/runs/{arm['run_id']}/artifacts", "artifacts")
    used = []

    def artifact(name):
        matches = [row for row in metadata if row.get("name") == name]
        require(len(matches) == 1, "missing/duplicate immutable producer artifact: " + name)
        row = matches[0]
        workflow_run = row.get("workflow_run", {})
        require(workflow_run.get("id") == arm["run_id"] and workflow_run.get("head_sha") == arm["sha"],
                "artifact belongs to another run/source")
        created = datetime.datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        started = datetime.datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        require(created >= started, "artifact predates selected run attempt")
        used.append({key: row[key] for key in ("id", "name", "digest", "created_at")})
        return api.artifact(row)

    plan = artifact(f"validation-plan-{arm['sha']}-{arm['run_attempt']}").json("validation-plan.json")
    require(plan.get("after") == arm["sha"], "coverage plan source differs")
    jobs = api.pages(f"/actions/runs/{arm['run_id']}/attempts/{arm['run_attempt']}/jobs", "jobs")
    job_ids = validate_jobs(jobs, expected_jobs(plan), arm)
    coverage = artifact(f"aggregate-source-platform-coverage-{arm['sha']}-{arm['run_attempt']}").json("source-coverage.json")
    require(coverage.get("status") == "PASS" and coverage.get("errors") == []
            and coverage.get("unresolved") == [], "affected whole-source coverage not proven")
    source_hash = source_snapshot(files)
    runtime_manifest = None
    runtime_archive = None
    for platform in PLATFORMS:
        owner_artifact = artifact(f"bootstrap-owner-{platform}-{arm['sha']}-{arm['run_attempt']}")
        owner, owner_inventory = validate_marker(owner_artifact, arm, platform, "integration", "bootstrap", source_hash)
        ordinary_artifact = artifact(f"pytest-marker-{platform}-not integration-{arm['run_id']}-{arm['run_attempt']}")
        validate_marker(ordinary_artifact, arm, platform, "not integration", "all", source_hash)
        remainder_artifact = artifact(f"pytest-marker-{platform}-integration-{arm['run_id']}-{arm['run_attempt']}")
        remainder, rest_inventory = validate_marker(remainder_artifact, arm, platform, "integration", "remainder", source_hash)
        owned, rest, original = map(set, (owner_inventory["selected"], rest_inventory["selected"], rest_inventory["original_expected"]))
        require(owned == set(rest_inventory["transferred"]) and not owned & rest and owned | rest == original
                and remainder.get("combined_integration_status") == "PASS"
                and remainder.get("manifest_sha256") == owner.get("manifest_sha256")
                and remainder.get("owner_status") == "PASS"
                and remainder.get("owner_guard_elapsed_s") == owner.get("guard_elapsed_s")
                and type(owner.get("guard_elapsed_s")) in (int, float)
                and math.isfinite(owner["guard_elapsed_s"]) and owner["guard_elapsed_s"] >= 0
                and 0 < remainder["timeout_s"] <= math.floor(1800 - owner["guard_elapsed_s"]),
                "owner/remainder reset the original integration budget or omitted nodes")
        if platform == "macos-arm64":
            runtime_archive = artifact(f"stage1-native-{platform}-{arm['sha']}-{arm['run_attempt']}")
            runtime_manifest = runtime_archive.json("manifest.json")
            require(runtime_manifest == owner["bundle"]
                    and hashlib.sha256(runtime_archive.bytes("manifest.json")).hexdigest() == owner.get("manifest_sha256"),
                    "same-run runtime bundle differs from original qualified input")
    return dict(source_sha=arm["sha"], source_tree=tree, source_sha256=source_hash,
                producer=lease(arm), job_ids=job_ids, artifacts=used), runtime_manifest, runtime_archive


def qualification_descriptor(spec):
    value = spec.get("qualification", {})
    path = Path(value.get("path", ""))
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and sha(value.get("sha256")) and digest(path) == value["sha256"], "qualification descriptor changed")
    data = decode(path.read_bytes())
    require(set(data) == {"schema", "repository", "baseline", "candidate"}
            and data["schema"] == "pcc.ci-ab-qualification.v1" and data["repository"] == REPOSITORY,
            "invalid qualification selectors")
    for arm in (data["baseline"], data["candidate"]):
        require(set(arm) == {"sha", "run_id", "run_attempt"} and sha(arm["sha"], 40)
                and type(arm["run_id"]) is int and arm["run_id"] > 0
                and type(arm["run_attempt"]) is int and arm["run_attempt"] > 0,
                "invalid exact run selector")
    require(data["candidate"] == dict(sha=os.environ.get("GITHUB_SHA"),
                                       run_id=int(os.environ["GITHUB_RUN_ID"]),
                                       run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
            and data["candidate"]["sha"] == spec.get("target_sha")
            and data["baseline"]["sha"] != data["candidate"]["sha"], "selected source/run identity differs")
    return data, path


def corpus_inputs(source, files, manifest):
    relative(manifest)
    require(manifest in files, "corpus manifest is not in the exact reviewed source")
    corpus = decode((Path(source) / manifest).read_bytes())
    require(set(corpus) == {"schema", "warmup", "inputs"} and corpus["schema"] == "pcc.ci-ab-inputs.v1"
            and isinstance(corpus["inputs"], list) and len(corpus["inputs"]) == 6, "six reviewed paired inputs required")
    def input_file(name):
        relative(name)
        require(name in files, "corpus input is not in the exact reviewed source")
        path = Path(source) / name
        return dict(path=str(path), sha256=digest(path))
    warmup = input_file(corpus["warmup"])
    inputs = []
    for row in corpus["inputs"]:
        require(set(row) == {"path", "expected_output"} and isinstance(row["expected_output"], str)
                and row["expected_output"].endswith("\n") and "\x00" not in row["expected_output"],
                "expected_output must contain complete stdout ending in a newline")
        inputs.append(dict(**input_file(row["path"]), expected_output=row["expected_output"]))
    require(len({row["sha256"] for row in inputs}) == 6, "paired inputs must have six distinct contents")
    return warmup, inputs


def verify_campaign(spec, *, baseline_files, candidate_files, deadline_monotonic=None):
    """Reauthenticate original gate evidence before building and measuring.

    No caller PASS labels, local cached API responses or Stage1-only exports can
    authorize execution. The cache contains only provider-digest-checked ZIPs;
    live provider metadata and every original job are re-read each invocation.
    """
    set_deadline(deadline_monotonic)
    data, path = qualification_descriptor(spec)
    selected = current_selection()
    require(selected["qualification"] == data, "qualification selectors differ from current dispatch")
    validate_source_pair(baseline_files, candidate_files)
    require(spec.get("allowed_changed_source") == SOURCE, "only reviewed scanner source may differ")
    for label, files in (("baseline", baseline_files), ("candidate", candidate_files)):
        source = spec[label + "_source"]
        require(tree_files(source["path"]) == files and tree_digest(files) == source["sha256"],
                "sealed source identity differs")
    warmup, inputs = corpus_inputs(spec["candidate_source"]["path"], candidate_files, selected["input_manifest"])
    require(spec.get("warmup") == warmup and spec.get("inputs") == inputs,
            "campaign inputs differ from the current dispatch's tracked corpus")
    api = GitHub(path.parent / "provider-cache")
    context = authenticate_context(api, selected, baseline_files, candidate_files)
    first, _, _ = verify_arm(api, data["baseline"], baseline_files, spec["baseline_source"]["path"], candidate=False, context=context)
    second, runtime, _ = verify_arm(api, data["candidate"], candidate_files, spec["candidate_source"]["path"], candidate=True, context=context)
    require(second["source_tree"] == context["candidate_tree"], "candidate tree identity differs")
    context["baseline_tree"] = first["source_tree"]
    archive = spec["runtime_archive"]
    runtime_path = runtime["paths"]["runtime"]
    expected_runtime = {Path(name).name: row["sha256"] for name, row in runtime["files"].items()
                        if name in {runtime_path, runtime_path + ".provenance.json", runtime_path + ".capi_syms"}}
    require(len(expected_runtime) == 3 and spec.get("runtime_files") == expected_runtime
            and archive["sha256"] == expected_runtime.get(Path(archive["path"]).name),
            "runtime differs from exact-source original qualification")
    for name, value in expected_runtime.items():
        file = Path(archive["path"]).parent / name
        require(file.is_file() and not file.is_symlink() and digest(file) == value, "qualified runtime bytes changed")
    admission = dict(schema="pcc.ci-ab-admission.v1", status="QUALIFIED_FOR_COMPILE_SLICE",
                     baseline=first, candidate=second, source_context=context,
                     stage2_le_stage1="UNAVAILABLE: no bounded matched full-stage campaign producer",
                     performance_result="UNRUN: qualification is not measurement")
    return frozen_proof(spec, admission, path.parent / "provider-cache")


def git(*arguments):
    require(not any(name.startswith("GIT_CONFIG_") for name in os.environ), "inherited Git runtime configuration")
    return subprocess.run(["git", *arguments], cwd=ROOT, check=True, capture_output=True,
                          timeout=min(30, remaining())).stdout


def snapshot(source, target):
    require(sha(source, 40) and git("rev-parse", "--verify", source + "^{commit}").decode().strip() == source,
            "exact source commit is not available")
    require(not target.exists(), "refuse stale source snapshot")
    # Stream to disk under the campaign's sampled guard. Never hold a full
    # multi-GiB source archive in RAM or execute an archive's hooks/code.
    archive_path = target.with_suffix(".tar")
    with archive_path.open("xb") as output:
        subprocess.run(["git", "archive", "--format=tar", source], cwd=ROOT,
                       stdout=output, stderr=subprocess.PIPE, check=True,
                       timeout=min(30, remaining()))
    require(archive_path.stat().st_size <= MAX_SOURCE, "source archive exceeds bound")
    target.mkdir()
    seen = set()
    total = 0
    with tarfile.open(archive_path) as archive:
        for member in archive:
            remaining()
            if member.isdir():
                continue
            relative(member.name)
            total += member.size
            require(member.isfile() and not member.issparse() and member.name not in seen
                    and 0 <= member.size <= MAX_SOURCE and total <= MAX_SOURCE,
                    "unsafe source archive member")
            seen.add(member.name)
            destination = target / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as incoming, destination.open("xb") as output:
                shutil.copyfileobj(incoming, output)
            destination.chmod(0o555 if member.mode & 0o111 else 0o444)
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    target.chmod(0o555)
    return tree_files(target)


def runtime_files(archive, manifest, target):
    path = manifest["paths"]["runtime"]
    names = {path, path + ".provenance.json", path + ".capi_syms"}
    target.mkdir()
    seen = set()
    with zipfile.ZipFile(archive.path) as outer, outer.open(archive.name("stage1-native.tar.gz")) as compressed:
        with tarfile.open(fileobj=compressed, mode="r|gz") as tar:
            total = 0
            for member in tar:
                relative(member.name)
                total += member.size
                require(member.isfile() and not member.issparse() and total <= MAX_ARCHIVE,
                        "invalid runtime transport member")
                if member.name not in names:
                    continue
                require(member.name not in seen, "duplicate runtime transport member")
                seen.add(member.name)
                destination = target / Path(member.name).name
                with tar.extractfile(member) as incoming, destination.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                row = manifest["files"][member.name]
                require(destination.stat().st_size == row["size"] and digest(destination) == row["sha256"],
                        "original runtime artifact bytes differ")
                destination.chmod(0o444)
    require(seen == names, "missing runtime archive or sidecar")
    return target / Path(path).name


def selected_dispatch(environ, event):
    """One selector adapter for request, preparation and every live recheck."""
    inputs = event.get("inputs", {})
    require(isinstance(inputs, dict) and environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and (inputs.get("performance_ab") is True
                 or (type(inputs.get("performance_ab")) is str and inputs["performance_ab"] == "true")),
            "performance opt-in not selected")
    ref = environ.get("GITHUB_REF", "")
    match = CANDIDATE_REF.fullmatch(ref)
    require(ref == MASTER_REF or match is not None, "not a reserved scanner candidate or master ref")
    # GitHub's payload schema allows a branch name; the runtime ref must still
    # be the exact full master/reserved ref. No arbitrary-ref normalization.
    require(event.get("ref") in (ref, ref[len("refs/heads/"):]), "dispatch event/ref differs")
    require(sha(inputs.get("expected_sha"), 40) and inputs["expected_sha"] == environ.get("GITHUB_SHA"),
            "A/B requires explicit expected_sha matching the current checkout")
    require(environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and environ.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@" + ref
            and environ.get("GITHUB_WORKFLOW_SHA") == inputs["expected_sha"],
            "actual workflow ref/SHA/repository differs")
    full_chain = inputs.get("full_chain", "false")
    require((full_chain is False or (type(full_chain) is str and full_chain == "false"))
            and inputs.get("pytest_selection", "both") == "both", "A/B requires both complete original marker groups")
    baseline = inputs.get("ab_baseline_sha", "")
    run = inputs.get("ab_baseline_run", "")
    require(sha(baseline, 40) and baseline != inputs["expected_sha"] and isinstance(run, str)
            and re.fullmatch(r"[1-9][0-9]{0,19}:[1-9][0-9]{0,19}", run),
            "baseline requires immutable SHA and run_id:attempt")
    manifest = inputs.get("ab_input_manifest", "")
    relative(manifest)
    run_id, attempt = map(int, run.split(":"))
    current = []
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        value = environ.get(name, "")
        require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value), "invalid current run/attempt")
        current.append(int(value))
    if match is not None:
        require(set(inputs) == {"performance_ab", "expected_sha", "ab_baseline_sha", "ab_baseline_run",
                               "ab_input_manifest", "scope", "base_sha", "full_chain", "pytest_selection"},
                "candidate dispatch requires all nine explicit selectors")
        require(match.group("baseline") == baseline and match.group("candidate") == inputs["expected_sha"]
                and int(match.group("run")) == run_id and int(match.group("attempt")) == attempt,
                "reserved candidate ref and selectors disagree")
        require(inputs.get("scope") == "all" and inputs.get("base_sha") == baseline
                and manifest == SCANNER_CORPUS,
                "candidate dispatch requires all original scope, exact base and shared scanner corpus")
    return dict(qualification=dict(schema="pcc.ci-ab-qualification.v1", repository=REPOSITORY,
                baseline=dict(sha=baseline, run_id=run_id, run_attempt=attempt),
                candidate=dict(sha=inputs["expected_sha"], run_id=current[0], run_attempt=current[1])),
                input_manifest=manifest,
                source_selection=dict(route="scanner-candidate" if match is not None else "master",
                                      candidate_ref=ref, event="workflow_dispatch",
                                      workflow_ref=environ["GITHUB_WORKFLOW_REF"],
                                      workflow_sha=environ["GITHUB_WORKFLOW_SHA"]))


def selectors(environ, event):
    return selected_dispatch(environ, event)["qualification"]


def current_selection():
    return selected_dispatch(os.environ, decode(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes()))


def validate_source_pair(baseline, candidate):
    require(set(baseline) == set(candidate)
            and [name for name in sorted(baseline) if baseline[name] != candidate[name]] == [SOURCE]
            and baseline[SOURCE]["executable"] == candidate[SOURCE]["executable"],
            "full sources must differ only at the same-mode reviewed scanner")


def read_ref(api, ref):
    require(ref == MASTER_REF or CANDIDATE_REF.fullmatch(ref) is not None, "unapproved source ref")
    row = api.get("/git/ref/" + ref[len("refs/"):])
    require(row.get("ref") == ref and row.get("object", {}).get("type") == "commit"
            and sha(row["object"].get("sha"), 40), "missing/malformed current provider ref")
    return row["object"]["sha"]


def baseline_ancestry(api, baseline, master):
    """Authenticate only ancestry of the immutable observed master, not its CI."""
    path = "/compare/" + baseline + "..." + master
    comparison = api.get(path + "?per_page=1")
    require(comparison.get("url") == "https://api.github.com/repos/" + REPOSITORY + path
            and comparison.get("base_commit", {}).get("sha") == baseline
            and comparison.get("merge_base_commit", {}).get("sha") == baseline
            and comparison.get("status") == "ahead" and type(comparison.get("behind_by")) is int
            and comparison["behind_by"] == 0 and type(comparison.get("ahead_by")) is int
            and comparison["ahead_by"] > 0 and type(comparison.get("total_commits")) is int
            and comparison["total_commits"] == comparison["ahead_by"],
            "observed master is not authenticated baseline ancestry; historical admission refused")
    return dict(baseline_sha=baseline, observed_master_sha=master, provider_url=comparison["url"],
                merge_base_sha=baseline, status="ahead", ahead_by=comparison["ahead_by"],
                behind_by=0, total_commits=comparison["total_commits"])


def authenticate_context(api, selected, baseline_files, candidate_files):
    """Authenticate immutable identities and live refs; never accept caller PASS."""
    validate_source_pair(baseline_files, candidate_files)
    data = selected["qualification"]
    selection = selected["source_selection"]
    candidate, baseline = data["candidate"]["sha"], data["baseline"]["sha"]
    commit = api.get("/git/commits/" + candidate)
    require(commit.get("sha") == candidate and sha(commit.get("tree", {}).get("sha"), 40)
            and isinstance(commit.get("parents"), list) and len(commit["parents"]) == 1
            and commit["parents"][0].get("sha") == baseline, "candidate must have sole parent baseline")
    workflow = api.get("/actions/workflows/" + WORKFLOW.rsplit("/", 1)[1])
    require(type(workflow.get("id")) is int and workflow["id"] > 0
            and workflow.get("path") == WORKFLOW and workflow.get("state") == "active",
            "provider workflow identity differs or is inactive")
    require(read_ref(api, selection["candidate_ref"]) == candidate, "candidate ref moved from selected SHA")
    master = read_ref(api, MASTER_REF)
    expected_master = baseline if selection["route"] == "scanner-candidate" else candidate
    ancestry = None
    if master != expected_master:
        require(selection["route"] == "scanner-candidate", "master candidate ref moved")
        ancestry = baseline_ancestry(api, baseline, master)
    require(WORKFLOW in baseline_files and WORKFLOW in candidate_files, "missing common workflow source")
    return dict(schema="pcc.ci-ab-source-context.v1", **selection, qualification=data,
                workflow=dict(id=workflow["id"], path=WORKFLOW, ref=selection["workflow_ref"],
                              sha=selection["workflow_sha"], sha256=candidate_files[WORKFLOW]["sha256"]),
                candidate_parent=baseline, candidate_tree=commit["tree"]["sha"],
                master_observations=[dict(phase="admission", sha=master)],
                expected_master=expected_master, observed_master_ancestry=ancestry,
                master_status="UNCHANGED_AT_ADMISSION" if master == expected_master else "STALE_FOR_PROMOTION",
                acceptance_scope="EXACT_B_C_PAIR_ONLY" if master == expected_master else "HISTORICAL_B_C_PAIR_ONLY",
                promotion="NOT_AUTHORIZED", transfer_to_new_master=False)


def finish_campaign(proof):
    """Outside timed pairs, inside the same deadline. Never promote anything.

    A changed candidate ref or unauthenticated master ancestry fails terminal
    acceptance. A proved advanced master does not invalidate immutable B/C
    evidence, but that result is historical only and cannot establish acceptance
    of the newer master or authorize promotion.
    """
    verify_frozen_campaign(proof)
    context = proof["admission"]["source_context"]
    selected = current_selection()
    require(selected == _LIVE_PROOFS[id(proof)]["selection"], "current dispatch selectors changed")
    api = GitHub(_LIVE_PROOFS[id(proof)]["cache"])
    candidate = context["qualification"]["candidate"]
    workflow = api.get("/actions/workflows/" + WORKFLOW.rsplit("/", 1)[1])
    require(workflow.get("id") == context["workflow"]["id"] and workflow.get("path") == WORKFLOW
            and workflow.get("state") == "active", "workflow changed during campaign")
    run = api.get(f"/actions/runs/{candidate['run_id']}/attempts/{candidate['run_attempt']}")
    validate_run(run, candidate, candidate=True, context=context)
    require(read_ref(api, context["candidate_ref"]) == candidate["sha"], "candidate ref moved during campaign")
    master = read_ref(api, MASTER_REF)
    require(context["route"] != "master" or master == candidate["sha"],
            "master candidate ref moved during final identity reads")
    ancestry = None
    baseline = context["qualification"]["baseline"]["sha"]
    if context["route"] == "scanner-candidate" and master != baseline:
        admitted = context["observed_master_ancestry"]
        if (admitted is not None and admitted["baseline_sha"] == baseline
                and admitted["observed_master_sha"] == master):
            # This exact immutable comparison is bound by the full frozen proof.
            ancestry = admitted
        else:
            ancestry = baseline_ancestry(api, baseline, master)
    observations = context["master_observations"] + [dict(phase="completed", sha=master)]
    stale = any(row["sha"] != context["expected_master"] for row in observations)
    return dict(context, master_observations=observations, final_master_ancestry=ancestry,
                final_identity_status="EXACT_PAIR_REAUTHENTICATED",
                master_status="STALE_FOR_PROMOTION" if stale else "UNCHANGED",
                acceptance_scope="HISTORICAL_B_C_PAIR_ONLY" if stale else "EXACT_B_C_PAIR_ONLY")


def set_deadline(deadline_monotonic):
    global _DEADLINE_MONOTONIC
    if deadline_monotonic is not None:
        require(type(deadline_monotonic) in (int, float) and math.isfinite(deadline_monotonic)
                and 0 < deadline_monotonic - time.monotonic() <= 12000, "invalid campaign deadline")
        _DEADLINE_MONOTONIC = (deadline_monotonic if _DEADLINE_MONOTONIC is None
                               else min(_DEADLINE_MONOTONIC, deadline_monotonic))
    remaining()


def frozen_proof(spec, admission, cache, *, extra_files=None):
    # This proof is returned by the live verifier in the same process. It is
    # never loaded as an authorization input from caller JSON or an artifact.
    files = {}
    for key in ("runtime_archive", "source_diff", "warmup", "qualification"):
        files[spec[key]["path"]] = spec[key]["sha256"]
    for row in spec["inputs"]:
        files[row["path"]] = row["sha256"]
    for name, value in spec["runtime_files"].items():
        files[str(Path(spec["runtime_archive"]["path"]).parent / name)] = value
    for path in Path(cache).glob("*.zip"):
        files[str(path)] = digest(path)
    for path in extra_files or ():
        files[str(path)] = digest(path)
    proof = dict(schema="pcc.ci-ab-frozen-proof.v1", admission=admission, files=files,
                 trees={spec[key]["path"]: spec[key]["sha256"]
                        for key in ("baseline_source", "candidate_source")})
    _LIVE_PROOFS[id(proof)] = dict(proof=proof, proof_sha256=tree_digest(proof),
                                  selection=current_selection(), cache=str(cache))
    return proof


def verify_frozen_campaign(proof):
    """Disk-only recheck of a proof returned by the live verifier this process."""
    registered = _LIVE_PROOFS.get(id(proof))
    require(registered is not None and registered["proof"] is proof
            and proof.get("schema") == "pcc.ci-ab-frozen-proof.v1"
            and tree_digest(proof) == registered["proof_sha256"],
            "missing or changed same-process live qualification proof")
    require(current_selection() == registered["selection"], "current dispatch selectors changed")
    remaining()
    for name, expected in proof["files"].items():
        path = Path(name)
        require(path.is_absolute() and path.is_file() and not path.is_symlink()
                and digest(path) == expected, "frozen qualification/input bytes changed")
    for name, expected in proof["trees"].items():
        require(tree_digest(tree_files(name)) == expected, "frozen source tree changed")


def write_request(path):
    selected = current_selection()
    needs = decode(os.environ["PCC_AB_NEEDS"].encode())
    require(set(needs) == set(ORIGINAL_JOBS)
            and all(row.get("result") == "success" for row in needs.values()),
            "original qualification dependency failed or skipped")
    require(not path.exists(), "refuse stale campaign request")
    save(path, dict(schema="pcc.ci-ab-request.v1", **selected))


def prepare_campaign(request_path, output_dir, deadline_monotonic):
    """Materialize authenticated inputs once, inside the caller's campaign guard.

    Returns (output_dir/prepared/campaign-spec.json, live_frozen_proof). The
    original absolute job deadline is not reset by preparation or any arm.
    """
    set_deadline(deadline_monotonic)
    request_path = Path(request_path)
    require(request_path.is_absolute() and request_path.is_file() and not request_path.is_symlink()
            and not request_path.stat().st_mode & 0o222, "request must be a sealed regular file")
    spec_request = decode(request_path.read_bytes())
    selected = current_selection()
    descriptor = selected["qualification"]
    require(spec_request == dict(schema="pcc.ci-ab-request.v1", **selected),
            "request differs from the explicit current dispatch")
    require(git("rev-parse", "HEAD").decode().strip() == descriptor["candidate"]["sha"], "checkout source differs")
    output = Path(output_dir) / "prepared"
    require(not output.exists() and output.is_absolute() and not output.is_relative_to(ROOT),
            "preparation requires fresh campaign-owned output outside checkout")
    output.mkdir(parents=True)
    status = dict(schema="pcc.ci-ab-preparation.v1", status="RUNNING", compile_slice="UNRUN",
                  stage2_le_stage1="UNAVAILABLE: bounded full-stage producer not implemented")
    try:
        sources, files = {}, {}
        for arm in ("baseline", "candidate"):
            path = output / (arm + "-source")
            files[arm] = snapshot(descriptor[arm]["sha"], path)
            sources[arm + "_source"] = dict(path=str(path), sha256=tree_digest(files[arm]))
        validate_source_pair(files["baseline"], files["candidate"])
        qualification = output / "qualification-selectors.json"
        save(qualification, descriptor)
        qualification.chmod(0o444)
        spec = dict(schema="pcc.ci-compile-ab.v1", target_sha=descriptor["candidate"]["sha"], **sources,
                    allowed_changed_source=SOURCE, qualification=dict(path=str(qualification), sha256=digest(qualification)))
        api = GitHub(output / "provider-cache")
        context = authenticate_context(api, selected, files["baseline"], files["candidate"])
        first, _, _ = verify_arm(api, descriptor["baseline"], files["baseline"], sources["baseline_source"]["path"], candidate=False, context=context)
        second, manifest, archive = verify_arm(api, descriptor["candidate"], files["candidate"], sources["candidate_source"]["path"], candidate=True, context=context)
        require(second["source_tree"] == context["candidate_tree"], "candidate tree identity differs")
        context["baseline_tree"] = first["source_tree"]
        admission = dict(schema="pcc.ci-ab-admission.v1", status="QUALIFIED_FOR_COMPILE_SLICE",
                         baseline=first, candidate=second, source_context=context,
                         stage2_le_stage1="UNAVAILABLE: no bounded matched full-stage campaign producer",
                         performance_result="UNRUN: qualification is not measurement")
        save(output / "qualification.json", admission)
        (output / "qualification.json").chmod(0o444)
        runtime = runtime_files(archive, manifest, output / "runtime")
        spec["runtime_archive"] = dict(path=str(runtime), sha256=digest(runtime))
        spec["runtime_files"] = {path.name: digest(path) for path in runtime.parent.iterdir()}
        spec["warmup"], spec["inputs"] = corpus_inputs(
            sources["candidate_source"]["path"], files["candidate"], selected["input_manifest"])
        diff = output / "source-variable.diff"
        diff.write_text("".join(difflib.unified_diff(
            (Path(sources["baseline_source"]["path"]) / SOURCE).read_text().splitlines(keepends=True),
            (Path(sources["candidate_source"]["path"]) / SOURCE).read_text().splitlines(keepends=True),
            fromfile="baseline/" + SOURCE, tofile="candidate/" + SOURCE, lineterm="\n")))
        diff.chmod(0o444)
        spec["source_diff"] = dict(path=str(diff), sha256=digest(diff))
        save(output / "campaign-spec.json", spec)
        (output / "campaign-spec.json").chmod(0o444)
        proof = frozen_proof(spec, admission, output / "provider-cache",
                             extra_files=(output / "campaign-spec.json", output / "qualification.json"))
        status["status"] = "PREPARED_NOT_MEASURED"
        return output / "campaign-spec.json", proof
    except BaseException as error:
        status.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(output / "preparation.json", status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-request", required=True, type=Path)
    args = parser.parse_args(argv)
    # This cheap selector check runs before the heavy campaign. It cannot build
    # or measure; full provider receipt admission occurs inside the guarded child.
    try:
        write_request(args.write_request)
    except BaseException as error:
        save(args.write_request.with_suffix(".failure.json"),
             dict(status="FAIL", error=type(error).__name__ + ": " + str(error)))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

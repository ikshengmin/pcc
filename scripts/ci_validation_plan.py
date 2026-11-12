"""Select one source-labelled CI campaign, without importing PCC or collecting tests.

Unknown source paths and deletions fail closed to original legacy qualification.
A frontend-only change never buys permission to skip a shared backend change.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

try:
    from .ci_validation_owners import additional_campaigns, read_manifest
except ImportError:
    from ci_validation_owners import additional_campaigns, read_manifest

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("cpp", "rust", "java", "go")
LANES = ("ordinary", "integration", "reference")
SCOPES = ("auto", "frontends", "cpp", "rust", "java", "go", "shared", "legacy", "all")
SHA = re.compile(r"[0-9a-f]{40}\Z")
CI_FILES = ("scripts/ci_validation", "scripts/data/ci_validation", ".github/workflows/")
NATIVE_PLATFORMS = (
    {"platform": "linux-x86_64", "runner": "ubuntu-24.04", "docker_platform": "linux/amd64"},
    {"platform": "linux-aarch64", "runner": "ubuntu-24.04-arm", "docker_platform": "linux/arm64"},
    {"platform": "windows-x86_64", "runner": "windows-2022"},
)


def requires_native_inputs(domain, lane):
    """Only these source lanes consume the existing same-run native artifacts."""
    return lane in LANES and (
        domain == "shared" or (lane == "integration" and domain in {"cpp", "ci-contracts"})
    )


def dispatch_full_chain(inputs):
    """Validate explicit fixed-point ownership before scheduling any producer."""
    value = inputs.get("full_chain", "false")
    if type(value) is bool:
        requested = value
    elif isinstance(value, str) and value in {"true", "false"}:
        requested = value == "true"
    else:
        raise ValueError("full_chain must be a boolean")
    selection = inputs.get("pytest_selection", "both")
    if selection not in {"both", "integration", "not integration"}:
        raise ValueError("unknown complete pytest selection")
    if requested and selection != "not integration":
        raise ValueError('full_chain=true requires pytest_selection="not integration"; '
                         'Linux/Windows producers own Stage2/3, with macOS integration mandatory')
    return requested


def select(changes, *, scope="auto", before=None, after=None, full_chain=False, ownership_manifest=None):
    if scope not in SCOPES:
        raise ValueError("unknown validation scope")
    if type(full_chain) is not bool:
        raise ValueError("full_chain must be a validated boolean")
    domains, reasons = set(), []
    legacy = False
    for status, path in changes:
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("invalid changed path")
        if status not in {"A", "M", "T"}:
            # No silent rename/delete omissions: the full old gates still own it.
            legacy = True
            domains.update((*LANGUAGES, "shared", "ci-contracts"))
            reasons.append({"path": path, "reason": "deleted/renamed/unknown change: full qualification"})
            continue
        language = next((name for name in LANGUAGES if path.startswith(
            (f"pcc/frontends/{name}/", f"tests/{name}/"))), None)
        if language:
            domains.add(language)
            reasons.append({"path": path, "reason": f"whole {language} source suite"})
        elif path.startswith(CI_FILES) or path == "tests/test_ci_validation.py":
            # A workflow/harness edit can alter an old gate itself. Its source
            # policy tests are not a substitute for executing that gate.
            legacy = True
            domains.update((*LANGUAGES, "shared", "ci-contracts"))
            reasons.append({"path": path, "reason": "validation policy: original legacy gates, all bounded domains and CI contracts"})
        elif (path.startswith("docs/") and not path.startswith("docs/research/") and
              Path(path).suffix.lower() in {".md", ".rst"}) or path in {"README.md", "AGENTS.md", "CONTEXT.md"}:
            reasons.append({"path": path, "reason": "documentation only"})
        else:
            # In particular runtime/shared/backend/ABI and unknown test paths
            # retain their original bootstrap/wheel/complete pytest owners.
            legacy = True
            domains.update((*LANGUAGES, "shared", "ci-contracts"))
            reasons.append({"path": path, "reason": "shared, legacy or unclassified: full qualification"})
    if scope in LANGUAGES:
        domains.add(scope)
    elif scope == "frontends":
        domains.update(LANGUAGES)
    elif scope == "shared":
        domains.update((*LANGUAGES, "shared"))
        legacy = True
    elif scope in {"legacy", "all"}:
        legacy = True
        domains.update((*LANGUAGES, "shared", "ci-contracts"))
    if full_chain:
        # An explicit fixed-point request owns every original platform even if
        # the changed paths alone would select only a source frontend.
        legacy = True
        domains.update((*LANGUAGES, "shared", "ci-contracts"))
    # Explicit manual selection may add obligations, never subtract changes.
    matrix = [{"domain": domain, "lane": lane} for domain in sorted(domains) for lane in LANES]
    manifest, manifest_digest = read_manifest(ROOT) if ownership_manifest is None else ownership_manifest
    platform_campaigns = additional_campaigns(sorted(domains), manifest)
    independent = [row for row in matrix if not requires_native_inputs(**row)]
    native = [row for row in matrix if requires_native_inputs(**row)]
    # A frontend project may require Stage1 without owning legacy qualification.
    # Reuse the existing producer; legacy work retains every original platform.
    producers = list(NATIVE_PLATFORMS) if legacy else list(NATIVE_PLATFORMS[:1]) if native else []
    return {"schema": 1, "before": before, "after": after, "scope": scope,
            "full_chain": full_chain, "changes": reasons, "domains": sorted(domains), "legacy": legacy,
            "cpp_platform": "cpp" in domains, "source": bool(matrix), "matrix": {"include": matrix},
            "source_independent": bool(independent), "matrix_independent": {"include": independent},
            "source_platform": bool(platform_campaigns), "matrix_platform": {"include": platform_campaigns},
            "ownership_manifest_sha256": manifest_digest,
            "source_native": bool(native), "matrix_native": {"include": native},
            "native_stage1": bool(producers), "matrix_native_stage1": {"include": producers}}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=30).stdout.strip()


def is_ancestor(before, after):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", before, after],
                            cwd=ROOT, check=False, capture_output=True, timeout=15)
    if result.returncode not in (0, 1):
        raise ValueError("cannot prove baseline ancestry")
    return result.returncode == 0


def qualified_base(runs, after, ancestor=is_ancestor):
    """Only an actual successful validation run, never the previous push tip."""
    for run in runs:
        sha = run.get("head_sha", "")
        if (run.get("event") == "push" and run.get("head_branch") == "master" and
                run.get("status") == "completed" and run.get("conclusion") == "success" and
                run.get("path", "").split("@", 1)[0] == ".github/workflows/pcc1-package-parity.yml" and
                SHA.fullmatch(sha) and sha != after and ancestor(sha, after)):
            return {key: run.get(key) for key in ("id", "run_attempt", "head_sha", "html_url", "conclusion", "path")}
    return None


def successful_validation_base(after):
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository identity")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    query = urllib.parse.urlencode({"branch": "master", "event": "push", "status": "success", "per_page": 100})
    url = api + "/repos/" + repository + "/actions/workflows/pcc1-package-parity.yml/runs?" + query
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=20) as response:
        runs = json.load(response)["workflow_runs"]
    # Bounded read only. No matching ancestor among these results is UNKNOWN,
    # not permission to use event.before or an unqualified interim commit.
    return qualified_base(runs, after)


def changed_paths(before, after):
    for value in (before, after):
        if not SHA.fullmatch(value) or value == "0" * 40:
            raise ValueError("exact nonzero before/after commit SHA required")
        if git("rev-parse", "--verify", value + "^{commit}") != value:
            raise ValueError("commit identity differs")
    if not is_ancestor(before, after):
        raise ValueError("batch base is not an ancestor of the selected revision")
    # --no-renames exposes both sides, including deletion, without guessing scope.
    raw = git("diff", "--name-status", "--no-renames", "-z", before, after, "--")
    fields = raw.split("\0") if raw else []
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise ValueError("malformed git change inventory")
    return list(zip(fields[::2], fields[1::2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    after = os.environ["GITHUB_SHA"]
    if not SHA.fullmatch(after) or git("rev-parse", "HEAD") != after:
        raise ValueError("checkout is not the exact event revision")
    name = os.environ["GITHUB_EVENT_NAME"]
    full_chain = dispatch_full_chain(event.get("inputs", {})) if name == "workflow_dispatch" else False
    before = None
    baseline = None
    baseline_error = None
    scope = "auto"
    if name in {"push", "workflow_dispatch"}:
        try:
            baseline = successful_validation_base(after)
            before = baseline["head_sha"] if baseline else None
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            baseline_error = type(error).__name__ + ": " + str(error)
    if name == "pull_request":
        before = event["pull_request"]["base"]["sha"]
        # Compare the actual merge checkout to its exact base, not a mutable ref.
    elif name == "workflow_dispatch":
        inputs = event.get("inputs", {})
        scope = inputs.get("scope", "frontends")
        expected = inputs.get("expected_sha", "")
        if expected and expected != after:
            raise ValueError("dispatch expected_sha differs from event revision")
        before = inputs.get("base_sha") or before
    elif name != "push":
        raise ValueError("unsupported validation event")
    try:
        changes = changed_paths(before or "", after)
    except (ValueError, subprocess.SubprocessError) as error:
        # New branch/unavailable parent cannot narrow the campaign.
        changes = [("UNKNOWN", "__unavailable_exact_base__")]
        plan = select(changes, scope=scope, before=before, after=after, full_chain=full_chain)
        plan["base_error"] = str(error)
    else:
        plan = select(changes, scope=scope, before=before, after=after, full_chain=full_chain)
    plan["context"] = {"repository": os.environ["GITHUB_REPOSITORY"], "sha": after,
                       "run_id": os.environ["GITHUB_RUN_ID"],
                       "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    plan["event_before_not_coverage_base"] = event.get("before")
    plan["qualified_baseline"] = baseline
    if baseline_error:
        plan["baseline_error"] = baseline_error
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key in ("legacy", "source", "cpp_platform", "matrix",
                    "source_independent", "matrix_independent", "source_native", "matrix_native",
                    "native_stage1", "matrix_native_stage1", "source_platform", "matrix_platform"):
            output.write(key + "=" + json.dumps(plan[key], separators=(",", ":")) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("## Exact-source validation plan\n\n```json\n" + json.dumps(plan, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()

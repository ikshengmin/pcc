"""Real filtered Git history must preserve exact-source CI planning, not qualify PCC."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import ci_validation_plan as plan

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True, timeout=15).stdout.strip()


class ValidationCheckoutTests(unittest.TestCase):
    def test_plan_checkout_preserves_history_budget_and_exact_revision(self):
        text = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
        job = text.split("  validation-plan:\n", 1)[1].split("\n  source-validation:", 1)[0]
        self.assertIn("timeout-minutes: 5", job)
        checkout = job.split("uses: actions/checkout@v5", 1)[1].split("      - uses:", 1)[0]
        self.assertIn("ref: ${{ github.sha }}", checkout)
        self.assertIn("fetch-depth: 0", checkout)
        self.assertIn("sparse-checkout: scripts", checkout)
        self.assertNotIn("filter:", checkout)  # Do not override checkout's sparse blob:none filter.
        self.assertIn("persist-credentials: false", checkout)

    def test_filtered_history_matches_full_plans_without_fetching_project_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            origin.mkdir()
            git(origin, "init", "-b", "master")
            git(origin, "config", "user.name", "CI fixture")
            git(origin, "config", "user.email", "ci@example.invalid")
            git(origin, "config", "uploadpack.allowFilter", "true")
            git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")

            def put(name, data):
                path = origin / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            def commit(message):
                git(origin, "add", "-A")
                git(origin, "commit", "-m", message)
                return git(origin, "rev-parse", "HEAD")

            for name in ("ci_validation_plan.py", "ci_validation_owners.py",
                         "data/ci_validation_owners.json"):
                put("scripts/" + name, (ROOT / "scripts" / name).read_bytes())
            put(".python-version", b"3.15\n")
            put("pcc/backend/owner.py", b"old owner\n")
            put("tests/rust/old.py", b"unchanged renamed content\n")
            put("projects/large/retired.bin", os.urandom(262144))
            base = commit("qualified source")
            put("pcc/backend/owner.py", b"new owner\n")
            skipped = commit("shared change [skip ci]")
            git(origin, "mv", "tests/rust/old.py", "tests/rust/new.py")
            (origin / "projects/large/retired.bin").unlink()
            put("docs/notes.md", b"documentation tip\n")
            tip = commit("documentation tip [skip ci]")
            git(origin, "checkout", "-b", "feature", base)
            put("pcc/frontends/cpp/parser.py", b"feature\n")
            commit("PR feature")
            git(origin, "checkout", "master")
            git(origin, "merge", "--no-ff", "feature", "-m", "PR merge")
            merge = git(origin, "rev-parse", "HEAD")
            git(origin, "checkout", "--orphan", "unrelated")
            git(origin, "rm", "-rf", ".")
            put("other.txt", b"not an ancestor\n")
            unrelated = commit("unrelated successful run")
            git(origin, "checkout", "master")

            full, sparse = root / "full", root / "sparse"
            git(root, "clone", "--no-checkout", origin.as_uri(), str(full))
            git(root, "clone", "--no-checkout", "--filter=blob:none", origin.as_uri(), str(sparse))
            git(sparse, "sparse-checkout", "set", "scripts")
            for repo in (full, sparse):
                git(repo, "checkout", "--detach", merge)
                # Any accidental lazy blob fetch now fails rather than hiding the regression.
                git(repo, "remote", "set-url", "origin", (root / "unavailable").as_uri())
            self.assertEqual(git(sparse, "rev-parse", "--is-shallow-repository"), "false")
            self.assertTrue((sparse / "scripts/ci_validation_plan.py").is_file())
            self.assertTrue((sparse / ".python-version").is_file())
            self.assertFalse((sparse / "pcc").exists())
            missing_before = git(sparse, "rev-list", "--objects", "--all", "--missing=print")
            self.assertTrue(any(line.startswith("?") for line in missing_before.splitlines()))
            full_bytes = sum(path.stat().st_size for path in (full / ".git/objects/pack").glob("*.pack"))
            sparse_bytes = sum(path.stat().st_size for path in (sparse / ".git/objects/pack").glob("*.pack"))
            self.assertGreater(full_bytes, 262144)
            self.assertLess(sparse_bytes, full_bytes)
            valid = dict(event="push", head_branch="master", status="completed",
                         conclusion="success", path=".github/workflows/pcc1-package-parity.yml")
            for event, before, after in (("push", base, tip), ("pull_request", tip, merge),
                                         ("workflow_dispatch", base, merge)):
                observed = []
                for repo in (full, sparse):
                    with patch.object(plan, "ROOT", repo):
                        self.assertTrue(plan.is_ancestor(before, after))
                        self.assertFalse(plan.is_ancestor(unrelated, after))
                        selected = plan.qualified_base(
                            [dict(valid, head_sha=unrelated, id=1), dict(valid, head_sha=base, id=2)],
                            after, ancestor=plan.is_ancestor)
                        self.assertEqual(selected["head_sha"], base)
                        changes = plan.changed_paths(before, after)
                        git(repo, "checkout", "--detach", after)
                        event_file, output = root / "event.json", root / "output.txt"
                        event_file.write_text(json.dumps({
                            "before": skipped,
                            "pull_request": {"base": {"sha": before}},
                            "inputs": {"scope": "auto", "base_sha": before, "expected_sha": after},
                        }))
                        environment = {
                            "GITHUB_EVENT_PATH": str(event_file), "GITHUB_SHA": after,
                            "GITHUB_EVENT_NAME": event, "GITHUB_REPOSITORY": "example/repository",
                            "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
                            "GITHUB_OUTPUT": str(output),
                        }
                        result_file = root / "plan.json"
                        with patch.dict(os.environ, environment), patch.object(
                            plan, "successful_validation_base", return_value=selected
                        ), patch.object(sys, "argv", ["plan", "--out", str(result_file)]):
                            # No ambient summary file may affect the fixture.
                            with patch.dict(os.environ):
                                os.environ.pop("GITHUB_STEP_SUMMARY", None)
                                plan.main()
                        actual = json.loads(result_file.read_text())
                        self.assertEqual(actual["before"], before)
                        self.assertEqual(actual["after"], after)
                        self.assertNotIn("base_error", actual)
                        observed.append((changes, actual))
                with self.subTest(event=event):
                    self.assertEqual(observed[0], observed[1])
                    if before == base:
                        self.assertIn(("M", "pcc/backend/owner.py"), observed[1][0])
                        self.assertIn(("D", "tests/rust/old.py"), observed[1][0])
                        self.assertIn(("A", "tests/rust/new.py"), observed[1][0])
                        self.assertIn(("D", "projects/large/retired.bin"), observed[1][0])
                        self.assertTrue(observed[1][1]["legacy"])
                        self.assertNotEqual(before, skipped)
            self.assertEqual(missing_before, git(sparse, "rev-list", "--objects", "--all", "--missing=print"))
            # Unavailable baselines must still select all gates, not silently
            # treat this as a fresh shallow checkout or the previous push tip.
            with patch.object(plan, "ROOT", sparse), patch.dict(os.environ, environment), patch.object(
                plan, "successful_validation_base", return_value=None
            ), patch.object(sys, "argv", ["plan", "--out", str(result_file)]):
                os.environ["GITHUB_EVENT_NAME"] = "push"
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
                plan.main()
            unknown = json.loads(result_file.read_text())
            self.assertTrue(unknown["legacy"])
            self.assertIn("base_error", unknown)
            self.assertEqual(set(unknown["domains"]), {*plan.LANGUAGES, "shared", "ci-contracts"})


if __name__ == "__main__":
    unittest.main()

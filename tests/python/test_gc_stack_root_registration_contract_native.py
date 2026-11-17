"""Current stack-registration elision contract on all five actual collectors.

This does not establish reclamation, a caller boundary, or resurrection safety.
Null slots exercise real registration/unregistration, without a new root policy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.owned_runtime_c_fixture import admitted_runtime, link_c_harness
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/fixtures/native/gc_stack_root_registration_contract.c"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.mark.integration
def test_stack_root_registration_contract_all_five_backends(tmp_path):
    # Deliberately one test: five fresh processes execute serially even under
    # the repository's default six pytest workers. Never provision a runtime.
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert manifest["members"]
    assert all(member["runtime_build_config"] == {"threads": True, "refcount": "atomic"}
               for member in manifest["members"])
    hashes = {str(path): _digest(path) for path in (SOURCE, archive, provenance)}
    binary = tmp_path / "stack_root_contract.out"
    link_c_harness(SOURCE, binary)
    identity = {"schema": "pcc.stack-root-registration-contract.v1",
                "target_triple": manifest["target_triple"],
                "hashes": hashes, "binary_sha256": _digest(binary),
                "runtime_provisioning": False, "gc_policy_changed": False}
    (tmp_path / "build.json").write_text(json.dumps(identity, indent=2) + "\n")
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend),
                           PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1")
        environment.pop("LC_ALL", None)
        result = run_process_group_timeout([str(binary)], env=environment, timeout=15)
        receipt = dict(identity, backend=backend, returncode=result.returncode,
                       stdout=result.stdout, stderr=result.stderr)
        (tmp_path / f"gc{backend}.json").write_text(json.dumps(receipt, indent=2) + "\n")
        assert result.returncode == 0, receipt
        assert result.stderr == "", receipt
        conservative = int(backend != 0)
        assert result.stdout == (
            f"stack-root-contract:ok backend={backend} stack={conservative} global=0 "
            f"native_stack={conservative} native_global=0 stack_sentinel={conservative} "
            "global_sentinel=0 distinct=1\n"
        ), receipt
    assert all(_digest(Path(path)) == digest for path, digest in hashes.items())

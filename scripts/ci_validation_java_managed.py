"""An opt-in Linux GC0 object for the shared Java ABI test, not a Stage1 member.

A producer verifies its existing Stage1 export in-place, then builds only the
optional product with the ordinary managed-runtime builder. The test campaign
only admits its three final files; it cannot invoke the producer to recover
from missing, stale or mismatched inputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

# The producer is invoked with -m; the existing validation coordinator also
# supports direct scripts/ci_validation_run.py execution without scripts on
# its package search path. Use the same imports in both public layouts.
if __package__:
    from . import ci_stage1_bundle as bundle, ci_validation_native_inputs as stage
else:
    import ci_stage1_bundle as bundle
    import ci_validation_native_inputs as stage

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-unknown-linux-gnu"
OBJECT = "py_shared_managed.o"
RECORD = OBJECT + ".provenance.json"
MANIFEST = "managed-input.json"
SOURCE = "pcc/runtime/py/py_shared_managed.py"
MAX_OBJECT = 16 * 1024 * 1024


def needed(domain, lane):
    return domain == "shared" and lane == "integration"


def checked_file(directory, name, max_bytes):
    path = directory / name
    if not 0 < bundle.regular_file(path, directory).st_size <= max_bytes:
        raise ValueError("managed input has an invalid size: " + name)
    return path


def object_identity(root, directory):
    # No compiler pipeline is imported merely to select an unrelated lane.
    from pcc.tools import runtime_archive_provenance as provenance
    from pcc.frontends.python.owned_runtime_build import runtime_build_config

    obj = checked_file(directory, OBJECT, MAX_OBJECT)
    record_path = checked_file(directory, RECORD, bundle.MAX_MANIFEST)
    record = json.loads(record_path.read_bytes())
    if not isinstance(record, dict):
        raise ValueError("invalid optional object provenance record")
    target = provenance._validate_member_record(record, member=OBJECT, member_bytes=obj.read_bytes(),
                                                runtime_root=root / "pcc/runtime")
    if (target != TARGET or record['source'] != SOURCE or
            record['codegen_checksum'] != provenance.codegen_checksum() or
            record['runtime_build_config'] != runtime_build_config() or
            record.get('object_emitter') != 'pcc-self-backend-object-writer' or
            record.get('uses_host_cc') is not False):
        raise ValueError("optional object source, emitter, target or build configuration differs")
    return {"file": OBJECT, "sha256": bundle.digest(obj),
            "provenance_sha256": bundle.digest(record_path),
            "source_sha256": record["source_sha256"], "codegen_sha256": record["codegen_checksum"]}


def binding(root, directory, native):
    if (bundle.host_platform() != "linux-x86_64" or native.get("owner") != "same_workflow_stage1_producer"
            or native["source"].get("clean_commit") is not True
            or native["source"]["commit"] != native["lease"]["sha"]):
        raise ValueError("managed input requires the verified Linux Stage1 native input")
    archive, _ = stage.verify_runtime(Path(native["runtime"]))
    return {"schema": "pcc.ci-java-managed-input.v1", "target": TARGET, "gc_backend": 0,
            "stage1_manifest_sha256": native["manifest_sha256"], "stage1_runtime_sha256": bundle.digest(archive),
            "source_sha256": native["source"]["source_sha256"], "lease": native["lease"],
            "object": object_identity(root, directory)}


def admit(root, directory, native):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("missing regular managed input directory")
    directory = directory.resolve(strict=True)
    if {p.name for p in directory.iterdir()} != {OBJECT, RECORD, MANIFEST}:
        raise ValueError("managed input requires exactly one object, its provenance and the binding manifest")
    manifest = checked_file(directory, MANIFEST, bundle.MAX_MANIFEST)
    record = json.loads(manifest.read_bytes())
    expected = binding(root, directory, native)
    if json.dumps(record, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("managed input differs from this source, Stage1 archive, run, or attempt")
    return {"directory": str(directory), "manifest_sha256": bundle.digest(manifest), "binding": record}


def consumer_inputs(root, directory, native, inherited):
    """Always select the dedicated node on its formal lane, even on failure.

    No invalid input ever reaches that node. The coordinator persists the
    error, runs unrelated original batches, and finally fails the campaign.
    """
    updates = {"PCC_CI_REQUIRE_JAVA_MANAGED_INPUT": "1", "PCC_JAVA_MANAGED_OBJECT": ""}
    try:
        if directory is None or not native or inherited:
            raise ValueError("Java managed input requires its companion and admitted Stage1, not ambient input")
        state = admit(root, directory, native)
        updates["PCC_JAVA_MANAGED_OBJECT"] = str(Path(state["directory"]) / OBJECT)
        return state, updates, None
    except Exception as error:
        return None, updates, type(error).__name__ + ": " + str(error)


def require_producer(root):
    # A source-only runner may import the selector without an installed PCC.
    # Neither the guard nor any compiler is needed until actual production.
    from pcc.driver.native_provisioning import require_native_provisioning_allowed
    require_native_provisioning_allowed(root)


def prepare_object(root, output):
    """Explicit producer only. The coordinator reservation always wins."""
    require_producer(root)
    if bundle.host_platform() != "linux-x86_64":
        raise ValueError("Java managed object qualification requires Linux x86-64 GC0")
    from pcc.runtime.shared_managed_build import managed_runtime_objects

    if output.exists() or output.is_symlink():
        raise ValueError("refuse a prior managed object output")
    with tempfile.TemporaryDirectory(prefix="pcc-java-managed-") as temporary:
        working = Path(temporary)
        result = managed_runtime_objects(str(root / "pcc/runtime"), str(working), TARGET, enabled=True, gc_backend=0)
        if result != [str(working / OBJECT)]:
            raise ValueError("optional production did not produce the selected object")
        checked = object_identity(root, working)
        record = json.loads((working / RECORD).read_bytes())
        if record["ir_sha256"] != bundle.digest((working / OBJECT).with_suffix(".ll")):
            raise ValueError("produced optional object IR provenance differs")
        # Transport only the two validated product files, not temporary IR.
        output.mkdir(parents=True)
        for name in (OBJECT, RECORD):
            (output / name).write_bytes((working / name).read_bytes())
    if object_identity(root, output) != checked:
        raise ValueError("newly published optional product bytes differ")
    return checked


def produce(root, stage1, output):
    # Production never clears an inherited guard and never imports a bundle
    # over an already populated producer workspace.
    require_producer(root)
    source = stage.source_identity()
    lease = bundle.workflow_lease()
    if source.get("clean_commit") is not True or source["commit"] != lease["sha"]:
        raise ValueError("managed input requires the producer's clean workflow revision")
    manifest = bundle.admit_in_place(root, stage1, source["commit"], "linux-x86_64", lease)
    if source["source_sha256"] != manifest["source"]["source_sha256"]:
        raise ValueError("managed input producer source differs from the exported Stage1")
    native = {"owner": "same_workflow_stage1_producer", "source": source, "lease": lease,
              "manifest_sha256": bundle.digest(stage1 / "manifest.json"), "runtime": str(root / manifest["paths"]["runtime"])}
    stage.verify_runtime(Path(native["runtime"]))
    prepare_object(root, output)
    record = binding(root, output, native)
    (output / MANIFEST).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if stage.source_identity() != source or bundle.digest(stage1 / "manifest.json") != native["manifest_sha256"]:
        raise ValueError("source or Stage1 manifest changed during optional production")
    return admit(root, output, native)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage1", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(produce(ROOT, args.stage1.resolve(strict=True), args.output), sort_keys=True))

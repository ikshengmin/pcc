"""Produce real PCC native-export/action metadata; no IR/native build or CI."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


MODULE_SUFFIXES = (
    "module_action_dag", "pipeline_context", "pipeline_exports",
    "pipeline_closed_world", "pipeline_ast_wire", "pipeline_import_scan",
    "pipeline_ir_text", "pipeline_modes", "pipeline_freestanding",
    "pipeline_profile", "pipeline_libpython", "pipeline_dependency_closure",
    "pipeline_frontend_workers", "pipeline_frontend_worker_execution",
    "pipeline_frontend_parallel", "export_meta", "raw_pointer_types",
    "py_ast", "py_parse", "py_lift", "private_names",
)


def build(source_root, output):
    sys.path.insert(0, str(source_root))
    from pcc.frontends.python import pipeline
    names = ["pcc.frontends.python." + name for name in MODULE_SUFFIXES]
    sources = [str(source_root / (name.replace(".", "/") + ".py")) for name in names]
    parsed, exports, derived = pipeline.build_closed_world_context(
        sources, names, merge_exports=True, allow_local_int_abi_proofs=False)
    dependencies = pipeline._closed_world_module_dependencies(parsed, names, sources, names)
    dependency_map = dict(dependencies)
    wire = output.with_suffix(".exports-wire.json")
    pipeline._write_native_exports_wire(str(wire), exports, derived)
    def digest(text):
        return hashlib.sha256(text.encode()).hexdigest()
    # This is metadata-only, not a runtime-build identity claim. Marker values
    # are identical in both arms and explicitly labeled in the payload.
    selected_sources = [(name, pipeline._module_action_dag.source_digest(path))
                        for name, path in zip(names, sources)]
    plan = {
        "compiler_digest": digest("metadata-only-selected-sources:" + json.dumps(selected_sources)),
        "runtime_abi_digest": digest("not-built-metadata-only"),
        "target": "host-metadata-only",
        "options_digest": digest("merge_exports=True;allow_local_int_abi_proofs=False"),
        "action_root": "metadata-only-not-published",
    }
    state = pipeline._build_python_frontend_action_state(sources, names, str(wire), plan, dependency_map)
    payload = {
        "claim": "real PCC host parser/exporter metadata; source revision requires external verification; no IR/native execution",
        "sources": [str(Path(path).relative_to(source_root)) for path in sources],
        "graph_state": state.payload(),
    }
    output.write_text(json.dumps(payload, sort_keys=True))
    print(json.dumps({"modules": len(names), "edges": sum(len(x.dependencies) for x in state.modules),
                      "metadata_bytes": output.stat().st_size, "export_wire_bytes": wire.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".exports-wire.json").exists():
        raise SystemExit("refusing to replace previous evidence")
    build(args.source_root.resolve(), args.output.resolve())

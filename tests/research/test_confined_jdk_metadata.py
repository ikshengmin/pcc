"""Final writer identity controls; parser/exporter is separately exercised once."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

from scripts.research.confined_jdk import metadata


def test_selected_source_content_controls_final_writer_identity(tmp_path, monkeypatch):
    root = tmp_path / "source"
    names = ["pcc.frontends.python.a", "pcc.frontends.python.b"]
    paths = [root / (name.replace(".", "/") + ".py") for name in names]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("answer = 1\n")
    monkeypatch.setattr(metadata, "MODULE_SUFFIXES", ("a", "b"))
    pipeline = ModuleType("pcc.frontends.python.pipeline")
    pipeline.build_closed_world_context = lambda *a, **k: (None, None, None)
    pipeline._closed_world_module_dependencies = lambda *a: [(name, []) for name in names]
    pipeline._write_native_exports_wire = lambda path, *a: Path(path).write_text("{}")
    pipeline._module_action_dag = SimpleNamespace(source_digest=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
    received = []

    def build_state(sources, supplied_names, wire, plan, dependencies):
        assert sources == [str(path) for path in paths] and supplied_names == names
        assert Path(wire).read_text() == "{}"
        received.append(plan.copy())
        return SimpleNamespace(modules=[SimpleNamespace(dependencies=()) for _ in names], payload=lambda: plan.copy())

    pipeline._build_python_frontend_action_state = build_state
    for module_name in ("pcc", "pcc.frontends", "pcc.frontends.python"):
        package = ModuleType(module_name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, module_name, package)
    monkeypatch.setattr(sys.modules["pcc.frontends.python"], "pipeline", pipeline, raising=False)
    monkeypatch.setitem(sys.modules, pipeline.__name__, pipeline)
    # build() intentionally inserts source_root; keep this test process isolated.
    monkeypatch.setattr(sys, "path", sys.path.copy())
    for index in range(3):
        if index == 2:
            paths[0].write_text("answer = 2\n")
        output = tmp_path / (str(index) + ".json")
        metadata.build(root, output)
        payload = json.loads(output.read_text())
        material = [(name, hashlib.sha256(path.read_bytes()).hexdigest()) for name, path in zip(names, paths)]
        expected = hashlib.sha256(("metadata-only-selected-sources:" + json.dumps(material)).encode()).hexdigest()
        assert payload["graph_state"]["compiler_digest"] == expected
        assert payload["sources"] == [str(path.relative_to(root)) for path in paths]
        assert "no IR/native execution" in payload["claim"]
    assert received[0] == received[1]
    assert received[1]["compiler_digest"] != received[2]["compiler_digest"]
    assert received[0]["runtime_abi_digest"] == hashlib.sha256(b"not-built-metadata-only").hexdigest()


def test_cli_refuses_overwriting_prior_evidence_before_importing_compiler(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve")
    completed = subprocess.run([sys.executable, metadata.__file__, "--source-root", str(tmp_path / "absent"),
                                "--output", str(output)], capture_output=True, text=True, timeout=5)
    assert completed.returncode != 0
    assert "refusing to replace previous evidence" in completed.stderr
    assert output.read_text() == "preserve"

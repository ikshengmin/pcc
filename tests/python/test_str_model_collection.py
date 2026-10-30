"""Keep runtime model ASTs out of test collection without changing coverage."""

from pathlib import Path


SOURCE = Path(__file__).with_name("test_str_method_slot_models.py")


def _load_model_module():
    # Execute the complete real module under a private name; no rewritten or
    # extracted implementation.  Its dependent adapter is already import-only.
    namespace = {"__name__": "_pcc_string_slot_collection_probe", "__file__": str(SOURCE)}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), namespace)
    return namespace


def test_string_model_import_does_not_read_or_parse_runtime_sources(monkeypatch):
    import test_set_call_slot_roots  # noqa: F401

    read_text = Path.read_text
    runtime_root = SOURCE.resolve().parents[2] / "pcc/runtime/py"
    runtime_sources = {
        runtime_root / name
        for name in ("py_class.py", "py_obj_ops_dispatch.py", "py_str_accessors.py")
    }

    def reject_runtime_read(path, *args, **kwargs):
        assert path not in runtime_sources, f"runtime source read during collection: {path}"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_runtime_read)
    namespace = _load_model_module()
    assert namespace["_TREES"] == {}
    assert namespace["_COMPILED"] == {}


def test_string_model_runtime_tree_is_lazy_cached_and_replaceable(monkeypatch, tmp_path):
    namespace = _load_model_module()
    source = tmp_path / "py_class.py"
    source.write_text("answer = 42\n")
    namespace["PORT"] = source
    reads = []
    read_text = Path.read_text

    def record_read(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record_read)
    assert namespace["_TREES"] == {}
    tree = namespace["_runtime_tree"]("py_class.py")
    assert tree.body[0].value.value == 42
    assert namespace["_runtime_tree"]("py_class.py") is tree
    assert reads == [source]

    # The find-descriptor consumer installs a fresh _TREES mapping per test.
    replacement = object()
    namespace["_TREES"] = {"py_class.py": replacement}
    assert namespace["_runtime_tree"]("py_class.py") is replacement
    assert reads == [source]

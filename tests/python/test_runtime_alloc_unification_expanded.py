from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).absolute().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_pcc_py_scalar_and_object_constructors_route_through_pcc_gc_alloc():
    checks = {
        "pcc/runtime/py/py_str.py": ["pcc_gc_alloc(40 + byte_len + 1, 4, 0)"],
        "pcc/runtime/py/py_obj_stubs.py": [
            "pcc_gc_alloc(24, 3, 0)",
            "pcc_gc_alloc(32, 16, 0)",
            "pcc_gc_alloc(24 + byte_len + 1, 17, 0)",
            "pcc_gc_alloc(24 + byte_len + 1, 18, 0)",
            "pcc_gc_alloc(24, 19, 0)",
        ],
        # ``PyClassObject`` is 120 bytes — header (24) + name (8) +
        # bases (8) + n_bases (8) + methods (8) + n_methods (8) +
        # del_method (8) + attrs (8) + metaclass (8) + various other
        # slots. The metaclass field at offset 112 brought the total
        # to 120 (was 112 before that field was added). Verified
        # against ``pcc/runtime/include/py_runtime.h`` and
        # ``pcc/runtime/src/py_internal.h::PyClassObject``.
        "pcc/runtime/py/py_class.py": [
            "pcc_gc_alloc(120, 10, 0)",
            "pcc_gc_alloc(size, load_i32(cls, 92), 0)",
        ],
        "pcc/runtime/py/py_weakref.py": ["pcc_gc_alloc(48, 21, 0)"],
        "pcc/runtime/py/py_exc_objects.py": ["pcc_gc_alloc(64, 12, 0)"],
    }
    for path, needles in checks.items():
        text = _text(path)
        for needle in needles:
            assert needle in text, f"{needle!r} missing from {path}"



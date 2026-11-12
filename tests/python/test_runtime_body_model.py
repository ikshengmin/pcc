"""The host model loader binds what production bodies read."""
from __future__ import annotations

from runtime_body_model import load_functions

from pcc.runtime.py import py_abi_constants


def test_load_functions_binds_generated_abi_constants_the_model_left_unbound(tmp_path):
    module = tmp_path / "abi_reader.py"
    module.write_text(
        "from pcc.runtime.py.py_abi_constants import (\n"
        "    PYOBJECTHEADER_TYPE_TAG_OFFSET,\n"
        "    PY_FLAG_IMMORTAL as IMMORTAL,\n"
        ")\n"
        "\n"
        "def layout():\n"
        "    return PYOBJECTHEADER_TYPE_TAG_OFFSET, IMMORTAL\n",
        encoding="utf-8",
    )
    namespace = load_functions(module, {"layout"}, {})
    assert namespace["layout"]() == (
        py_abi_constants.PYOBJECTHEADER_TYPE_TAG_OFFSET,
        py_abi_constants.PY_FLAG_IMMORTAL,
    )
    # A model's own binding of the name wins.
    namespace = load_functions(module, {"layout"}, {"IMMORTAL": 99})
    assert namespace["layout"]() == (py_abi_constants.PYOBJECTHEADER_TYPE_TAG_OFFSET, 99)

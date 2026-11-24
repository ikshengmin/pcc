"""Native regression: boxed valueclass pointer fields survive collection."""
from __future__ import annotations

import os
import subprocess

import pytest


_PROGRAM = (
    "import gc\n"
    "import pcc\n"
    "from typing import Any\n"
    "@pcc.valueclass\n"
    "class Bag:\n"
    "    items: list\n"
    "    label: str\n"
    "    extra: list\n"
    "\n"
    "def ident(x: Any) -> Any:\n"
    "    return x\n"
    "\n"
    "def check_payload(box: Any) -> None:\n"
    "    box.items.append(8)\n"
    "    gc.collect()\n"
    "    box.extra.append('tail')\n"
    "    gc.collect()\n"
    "    print(len(box.items))\n"
    "    print(box.items[3])\n"
    "    print(box.label)\n"
    "    print(len(box.extra))\n"
    "    print(box.extra[1])\n"
    "\n"
    "def main() -> None:\n"
    "    bag = Bag([1, 2, 3], 'bag', ['head'])\n"
    "    boxed = ident(bag)\n"
    "    gc.collect()\n"
    "    check_payload(boxed)\n"
    "\n"
    "main()\n"
)


@pytest.fixture(scope="module")
def _valueclass_pointer_payload_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_valueclass_pointer_payload")
    src = tmp / "valueclass_pointer_payload.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "valueclass_pointer_payload_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


def test_valueclass_pointer_payload_survives_collection(_valueclass_pointer_payload_exe):
    run = subprocess.run(
        [_valueclass_pointer_payload_exe],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["4", "8", "bag", "2", "tail"], run.stdout

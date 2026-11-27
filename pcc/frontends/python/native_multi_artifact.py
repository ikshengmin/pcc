"""Private IR-only native Python multi-module request boundary.

The ordered inputs are explicit API inputs, not single-entry CLI discovery.
Only fresh IR output is transported; existing output, executable, link and
profile support require their own contracts.  The real compiler owns validation and emission.
"""

from __future__ import annotations

import hashlib
import json
import os

REQUEST_SCHEMA = "pcc.python-multi-ir-request.v1"
RESPONSE_SCHEMA = "pcc.python-multi-ir-response.v1"
REQUEST_FIELDS = (
    "schema", "src_paths", "out_path", "module_names", "entry_module",
    "emit_llvm_only", "verbose", "ir_scaffold_mode", "backend", "target_triple",
    "recursive_stdlib", "runtime_archive",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_request(request):
    _require(isinstance(request, dict) and set(request) == set(REQUEST_FIELDS),
             "Python multi IR request fields differ")
    _require(request["schema"] == REQUEST_SCHEMA,
             "Python multi IR request schema differs")
    _require(request["emit_llvm_only"] is True and request["backend"] == "self",
             "Python multi worker supports only self IR output")
    for key in ("verbose", "recursive_stdlib"):
        _require(type(request[key]) is bool, "invalid Python multi boolean: " + key)
    _require(isinstance(request["src_paths"], list)
             and all(isinstance(p, str) for p in request["src_paths"]),
             "invalid Python multi source sequence")
    modules = request["module_names"]
    _require(modules is None or (isinstance(modules, list)
             and all(isinstance(m, str) for m in modules)),
             "invalid Python multi module names")
    _require(isinstance(request["out_path"], str), "invalid Python multi output path")
    for key in ("entry_module", "ir_scaffold_mode", "target_triple", "runtime_archive"):
        value = request[key]
        _require(value is None or isinstance(value, str),
                 "invalid Python multi field: " + key)
    return request


def compile_request(request):
    from pcc.frontends.python.pipeline import compile_python_multi

    validate_request(request)
    compile_python_multi(
        request["src_paths"], request["out_path"],
        verbose=request["verbose"], emit_llvm_only=True,
        entry_module=request["entry_module"], module_names=request["module_names"],
        ir_scaffold_mode=request["ir_scaffold_mode"], backend="self",
        target_triple=request["target_triple"], recursive_stdlib=request["recursive_stdlib"],
        runtime_archive=request["runtime_archive"],
    )


def run_native_multi_ir(request_path, response_path):
    """Write a fresh success receipt only after actual combined IR emission."""
    _require(os.path.abspath(request_path) != os.path.abspath(response_path),
             "Python multi request aliases response")
    _require(not os.path.lexists(response_path), "Python multi response already exists")
    with open(request_path, "rb") as stream:
        raw = stream.read()
    request = validate_request(json.loads(raw.decode("utf-8")))
    output = os.path.abspath(request["out_path"])
    _require(output != os.path.abspath(request_path) and output != os.path.abspath(response_path),
             "Python multi IR output aliases transport")
    _require(not os.path.lexists(output), "Python multi IR output already exists")
    compile_request(request)
    _require(os.path.isfile(output), "Python multi compiler emitted no IR output")
    with open(response_path, "x", encoding="utf-8") as stream:
        json.dump({"schema": RESPONSE_SCHEMA, "request_sha256": hashlib.sha256(raw).hexdigest(),
                   "outcome": "passed"}, stream)
        stream.write("\n")
    return 0

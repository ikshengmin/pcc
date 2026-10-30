"""One native C translation-unit artifact, using the existing real frontend.

This is a file boundary for the private compiler worker, not an IR parser or
an alternative C implementation. The normal host API and its cache stay intact.
"""
from __future__ import annotations

import hashlib
import json
import os

REQUEST_SCHEMA = "pcc.c-unit-request.v1"
RESPONSE_SCHEMA = "pcc.c-unit-response.v1"
REQUEST_FIELDS = (
    "schema", "unit", "base_dir", "use_system_cpp", "include_dirs", "cpp_args",
    "cache_dir", "use_compile_cache", "frontend_opt_level", "backend_sig",
    "target_triple", "fsanitize", "evaluation_entry",
)
ARTIFACT_FIELDS = (
    "unit_name", "ir_text", "return_type", "external_defs", "func_return_types",
    "function_symbols", "pass_stats", "pass_report",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _optional_text(value):
    return value is None or isinstance(value, str)


def _optional_strings(value):
    return value is None or (isinstance(value, list) and all(isinstance(x, str) for x in value))


def validate_request(request):
    _require(isinstance(request, dict) and set(request) == set(REQUEST_FIELDS),
             "C artifact request fields differ")
    _require(request["schema"] == REQUEST_SCHEMA, "C artifact request schema differs")
    unit = request["unit"]
    _require(isinstance(unit, dict) and set(unit) == {"name", "path", "source"},
             "C translation-unit fields differ")
    _require(isinstance(unit["name"], str) and isinstance(unit["source"], str)
             and _optional_text(unit["path"]), "invalid C translation-unit values")
    for key in ("base_dir", "cache_dir", "backend_sig", "target_triple", "evaluation_entry"):
        _require(_optional_text(request[key]), "invalid C request field: " + key)
    for key in ("include_dirs", "cpp_args"):
        _require(_optional_strings(request[key]), "invalid C request sequence: " + key)
    _require(_optional_strings(request["fsanitize"]) or isinstance(request["fsanitize"], str),
             "invalid C request sequence: fsanitize")
    # Preserve the job's actual truthiness and int(opt_level) behavior. JSON
    # values that are invalid there must reach its genuine native rejection;
    # this wire validator must not substitute a different semantic error.
    return request


def _validate_type(value):
    if value is None:
        return
    _require(isinstance(value, (tuple, list)) and bool(value), "invalid C return descriptor")
    kind = value[0]
    if kind in ("void", "float", "double"):
        _require(len(value) == 1, "invalid scalar C return descriptor")
    elif kind == "int":
        _require(len(value) == 2 and type(value[1]) is int and value[1] > 0,
                 "invalid integer C return descriptor")
    elif kind == "ptr":
        _require(len(value) == 2, "invalid pointer C return descriptor")
        _validate_type(value[1])
    else:
        raise ValueError("unknown C return descriptor")


def _pack_type(value):
    if value is None:
        return None
    items = list(value)
    if items[0] == "ptr":
        items[1] = _pack_type(items[1])
    return {"container": "tuple" if isinstance(value, tuple) else "list", "items": items}


def _unpack_type(value):
    if value is None:
        return None
    _require(isinstance(value, dict) and set(value) == {"container", "items"}
             and value["container"] in ("tuple", "list") and isinstance(value["items"], list)
             and bool(value["items"]), "invalid C wire return descriptor")
    items = list(value["items"])
    if items[0] == "ptr":
        _require(len(items) == 2, "invalid C wire pointer descriptor")
        items[1] = _unpack_type(items[1])
    result = tuple(items) if value["container"] == "tuple" else items
    _validate_type(result)
    return result


def validate_artifact(artifact, unit_name):
    # Require real producer fields, never fill missing metadata from host IR.
    _require(isinstance(artifact, dict) and set(artifact) == set(ARTIFACT_FIELDS),
             "C artifact fields differ")
    _require(artifact["unit_name"] == unit_name and isinstance(artifact["ir_text"], str),
             "C artifact unit or IR differs")
    _require(isinstance(artifact["external_defs"], list), "C external definitions missing")
    for item in artifact["external_defs"]:
        _require(isinstance(item, list) and len(item) == 3
                 and item[0] in ("function", "object") and all(isinstance(x, str) for x in item),
                 "invalid C external definition")
    _validate_type(artifact["return_type"])
    _require(isinstance(artifact["pass_stats"], str), "C artifact pass statistics differ")
    for key in ("func_return_types", "function_symbols", "pass_report"):
        _require(isinstance(artifact[key], dict), "C artifact metadata differs: " + key)
    for name, value in artifact["func_return_types"].items():
        _require(isinstance(name, str), "invalid C function name")
        _validate_type(value)
    for name, symbol in artifact["function_symbols"].items():
        _require(isinstance(name, str) and isinstance(symbol, str), "invalid C function symbol")
    return artifact


def artifact_to_wire(artifact):
    """Preserve fresh tuple descriptors and cache-hit list descriptors exactly."""
    validate_artifact(artifact, artifact["unit_name"])
    result = dict(artifact)
    result["return_type"] = _pack_type(artifact["return_type"])
    result["func_return_types"] = {name: _pack_type(value)
                                   for name, value in artifact["func_return_types"].items()}
    return result


def artifact_from_wire(artifact, unit_name):
    _require(isinstance(artifact, dict) and set(artifact) == set(ARTIFACT_FIELDS),
             "C wire artifact fields differ")
    _require(isinstance(artifact["func_return_types"], dict), "C wire function types missing")
    result = dict(artifact)
    result["return_type"] = _unpack_type(artifact["return_type"])
    result["func_return_types"] = {name: _unpack_type(value)
                                   for name, value in artifact["func_return_types"].items()}
    return validate_artifact(result, unit_name)


def compile_request(request):
    """Preserve the job's actual source/options; do not reread unit.path."""
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import _compile_translation_unit_artifact_job

    validate_request(request)
    value = request["unit"]
    unit = TranslationUnit(name=value["name"], path=value["path"], source=value["source"])
    artifact = _compile_translation_unit_artifact_job(
        unit, request["base_dir"], request["use_system_cpp"],
        request["include_dirs"], request["cpp_args"], request["cache_dir"],
        request["use_compile_cache"], request["frontend_opt_level"],
        request["backend_sig"], request["target_triple"], request["fsanitize"],
        request["evaluation_entry"],
    )
    return validate_artifact(artifact, unit.name)


def run_native_c_artifact(request_path, output_path):
    """Write one actual artifact or a truthful native failure, never overwrite."""
    from pcc.frontends.c.parse.plyparser import ParseError

    _require(os.path.abspath(request_path) != os.path.abspath(output_path),
             "C artifact request aliases output")
    _require(not os.path.lexists(output_path), "C artifact output already exists")
    with open(request_path, "rb") as stream:
        raw = stream.read()
    response = {"schema": RESPONSE_SCHEMA,
                "request_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        request = json.loads(raw.decode("utf-8"))
        artifact = compile_request(request)
        response.update(outcome="passed", artifact=artifact_to_wire(artifact))
        status = 0
    except Exception as error:
        # Exact known frontend type is classified using its real class, not a
        # caller-provided module name or arbitrary dynamic-import instruction.
        kind = "ParseError" if isinstance(error, ParseError) else type(error).__name__
        response.update(outcome="failed", error_type=kind, error_message=str(error))
        status = 1
    # The caller owns a fresh private directory. Exclusive creation refuses a
    # pre-existing file/symlink; a killed partial write cannot be read as PASS.
    with open(output_path, "x", encoding="utf-8") as stream:
        json.dump(response, stream, ensure_ascii=True)
        stream.write("\n")
    return status

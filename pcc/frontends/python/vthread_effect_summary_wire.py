"""Deterministic line codec for compact virtual-thread effect summaries."""

from __future__ import annotations

import json


SCHEMA = "pcc.vthread.effect-summary.v1"


def _validated_text(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.find("\t") >= 0
        or value.find("\n") >= 0
        or value.find("\r") >= 0
    ):
        raise ValueError("invalid vthread effect summary " + label)
    return value


def _validate_values(values: list[str], label: str) -> None:
    if not isinstance(values, list):
        raise ValueError("invalid vthread effect summary " + label)
    index = 0
    while index < len(values):
        _validated_text(values[index], label)
        index += 1


def write_summary(
    path: str,
    module_name: str,
    seeds: list[str],
    edges: list[str],
    publish: list[str],
) -> None:
    module_name = _validated_text(module_name, "module")
    _validate_values(seeds, "seed")
    _validate_values(edges, "edge")
    _validate_values(publish, "publish")
    if len(edges) % 2:
        raise ValueError("vthread effect summary edge payload is odd")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(SCHEMA + "\n")
        stream.write("M\t" + module_name + "\n")
        for key in seeds:
            stream.write("S\t" + key + "\n")
        index = 0
        while index < len(edges):
            stream.write("E\t" + edges[index] + "\t" + edges[index + 1] + "\n")
            index += 2
        for key in publish:
            stream.write("P\t" + key + "\n")


def read_summary(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    if not lines or lines[0] != SCHEMA:
        raise ValueError("invalid vthread effect summary wire schema")
    module_name = ""
    seeds: list[str] = []
    edges: list[str] = []
    publish: list[str] = []
    index = 1
    while index < len(lines):
        parts = lines[index].split("\t")
        if len(parts) == 2 and parts[0] == "M":
            if module_name:
                raise ValueError("duplicate vthread effect summary module")
            module_name = _validated_text(parts[1], "module")
        elif len(parts) == 2 and parts[0] == "S":
            seeds.append(_validated_text(parts[1], "seed"))
        elif len(parts) == 3 and parts[0] == "E":
            edges.append(_validated_text(parts[1], "edge caller"))
            edges.append(_validated_text(parts[2], "edge callee"))
        elif len(parts) == 2 and parts[0] == "P":
            publish.append(_validated_text(parts[1], "publish"))
        else:
            raise ValueError("invalid vthread effect summary wire row")
        index += 1
    if not module_name:
        raise ValueError("vthread effect summary module is missing")
    return {
        "module_name": module_name,
        "seeds": seeds,
        "edges": edges,
        "publish": publish,
    }


EFFECT_EXPORT_SCHEMA = "pcc.vthread.effect-exports.indexed.v1"


class _LazyEffectExportRow:
    """A private, unmaterialized module row in one summary worker's map."""

    def __init__(self, row: str) -> None:
        self.row: str = row


def _validated_effect_export_module(value: str) -> str:
    value = _validated_text(value, "export module")
    if value.find("\x00") >= 0:
        raise ValueError("invalid vthread effect export module")
    return value


def write_effect_exports(path: str, native_exports) -> None:
    """Publish independently decodable rows from the private effect surface."""
    from pcc.frontends.python.pipeline_exports import _native_export_to_wire

    # Render before opening the destination, as the legacy writer does.
    lines = []
    for module_name, table in native_exports.items():
        name = _validated_effect_export_module(module_name)
        if not isinstance(table, dict):
            raise ValueError("invalid vthread effect export module table")
        lines.append(name + "\t" + json.dumps(_native_export_to_wire(table)) + "\n")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(EFFECT_EXPORT_SCHEMA + "\n")
        for line in lines:
            stream.write(line)


def read_effect_exports(path: str):
    """Index complete module inventory, lazily materializing accessed rows.

    Legacy inputs remain readable for replay and alternate writer callbacks.
    New-format headers and duplicates are validated eagerly. As with indexed
    codegen exports, JSON payload validation belongs to the selected row.
    """
    with open(path, "r", encoding="utf-8") as stream:
        text = stream.read()
    if not text.startswith(EFFECT_EXPORT_SCHEMA + "\n"):
        from pcc.frontends.python.pipeline_exports import _decode_native_exports_wire
        return _decode_native_exports_wire(text)
    lines = text.splitlines()
    exports = {}
    for row in lines[1:]:
        tab = row.find("\t")
        if tab < 0:
            raise ValueError("invalid vthread effect export row")
        module_name = _validated_effect_export_module(row[:tab])
        if module_name in exports:
            raise ValueError("duplicate vthread effect export module")
        exports[module_name] = _LazyEffectExportRow(row)
    return exports, {}


def effect_module_exports(native_exports: dict, module_name: str):
    """Ordinary tables are unchanged; a private selected row decodes once."""
    exports = native_exports.get(module_name, {})
    if isinstance(exports, _LazyEffectExportRow):
        from pcc.frontends.python.pipeline_exports import _native_export_from_owned_wire

        row = exports.row
        try:
            exports = _native_export_from_owned_wire(json.loads(row[row.find("\t") + 1 :]))
        except Exception as exc:
            raise ValueError("invalid vthread effect export module payload") from exc
        if not isinstance(exports, dict):
            raise ValueError("invalid vthread effect export module payload")
        native_exports[module_name] = exports
    return exports


__all__ = ["SCHEMA", "read_summary", "write_summary", "EFFECT_EXPORT_SCHEMA",
           "read_effect_exports", "write_effect_exports", "effect_module_exports"]

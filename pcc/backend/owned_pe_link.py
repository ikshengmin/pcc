"""Native Windows link entry; all object, import and PE work is in process."""

import os

from .coff_x86_64 import assemble, parse_object, CoffError
from .pe_x86_64 import link_executable
from .macho_internal_inputs import read_internal_input_manifest
from .self_backend_x86_64_windows import stack_probe_assembly


def link_inputs(*, output, assembly=(), objects=(), archives=(), manifest="",
                entry="pcc_windows_start"):
    if manifest and (assembly or objects):
        raise CoffError("manifest cannot be combined with direct internal inputs")
    ordered = read_internal_input_manifest(manifest) if manifest else (
        [("ASM", path) for path in assembly] + [("PCO", path) for path in objects]
    )
    inputs = []
    for kind, path in ordered:
        if os.path.abspath(path) == os.path.abspath(output):
            raise CoffError("link output aliases input")
        with open(path, "rb") as stream:
            data = stream.read()
        inputs.append(assemble(data.decode("utf-8")) if kind == "ASM" else parse_object(data))
    inputs.append(assemble(stack_probe_assembly()))
    archive_data = []
    for path in archives:
        with open(path, "rb") as stream:
            archive_data.append(stream.read())
    image = link_executable(inputs, archives=archive_data, entry=entry)
    temporary = output + ".pcc-link.tmp"
    try:
        with open(temporary, "wb") as stream:
            stream.write(image)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

"""Native Mach-O linker entry over pcc's existing parser/assembler/linker.

Accepts --out PATH and repeated --native-object, --object, --asm or --archive
inputs. Unsupported CLI surfaces fail explicitly. This entry is qualified
separately before the compiler may select it instead of its host link helper.
"""

import os
import sys

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.native_object import NativeObject, decode_packed_native_object
from pcc.backend.macho_internal_inputs import read_internal_input_manifest


def main(argv=None) -> None:
    if argv is None:
        argv = sys.argv
    output = ""
    entry = "_main"
    objects = []
    archives = []
    manifest = ""
    index = 1
    while index < len(argv):
        option = argv[index]
        if index + 1 >= len(argv):
            raise ValueError("missing value for " + option)
        value = argv[index + 1]
        index += 2
        if option == "--out":
            output = value
        elif option == "--entry":
            entry = value
        elif option == "--internal-input-manifest":
            if manifest:
                raise ValueError("native linker accepts only one internal input manifest")
            manifest = value
        elif option == "--asm":
            with open(value, "r") as stream:
                assembly = stream.read()
            sections, undefined = assemble_file(assembly)
            objects.append(NativeObject.from_sections(sections, undefined=undefined))
        elif option in ("--native-object", "--object", "--archive"):
            with open(value, "rb") as stream:
                data = stream.read()
            if option == "--native-object":
                objects.append(decode_packed_native_object(data))
            elif option == "--archive":
                archives.append(data)
            else:
                objects.append(data)
        else:
            raise ValueError("unsupported native linker option: " + option)
    if manifest:
        if objects:
            raise ValueError("internal input manifest cannot be combined with direct inputs")
        for kind, path in read_internal_input_manifest(manifest):
            if kind == "PCO":
                with open(path, "rb") as stream:
                    objects.append(decode_packed_native_object(stream.read()))
            else:
                with open(path, "r") as stream:
                    sections, undefined = assemble_file(stream.read())
                objects.append(NativeObject.from_sections(sections, undefined=undefined))
    if not output or not objects:
        raise ValueError("native linker requires --out and object/assembly input")
    image = link_executable(objects, archives=archives, entry=entry, _consume_inputs=True)
    temporary = output + ".tmp"
    with open(temporary, "wb") as stream:
        stream.write(image)
    os.chmod(temporary, 0o755)
    os.replace(temporary, output)


if __name__ == "__main__":
    main()

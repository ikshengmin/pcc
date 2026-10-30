"""Mach-O extension-symbol formats built from real owned objects.

These host tests do not invoke a compiler, external linker, dynamic loader, or
native program. They qualify object/archive closure and emitted byte contracts,
not Darwin execution or the five-GC extension lifetime boundary.
"""

from __future__ import annotations

import hashlib
import struct
import unittest
from unittest.mock import patch

from pcc.backend import BackendUnavailable, macho_exec, macho_shared
from pcc.backend import macho_spec as spec
from pcc.backend.macho_codesign import parse_signature
from pcc.backend.macho_link import LinkError
from pcc.backend.macho_obj import (
    Relocation,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
)
from pcc.backend.native_object import NativeObject


_TARGET = "aarch64-apple-darwin"
_RET = b"\xc0\x03\x5f\xd6"
_BL = b"\0\0\0\x94"


def _object():
    return NativeObject.from_sections([
        Section(
            "__text", "__TEXT", _BL + _RET + _RET, align_log2=2,
            flags=TEXT_SECTION_FLAGS,
            symbols=(TextSymbol("_local", 0, external=False),
                     TextSymbol("_private", 0, private_external=True),
                     TextSymbol("_main", 0), TextSymbol("_callee", 8)),
            relocations=(Relocation(0, "_callee", spec.ARM64_RELOC_BRANCH26, True),),
        ),
        Section("__data", "__DATA", struct.pack("<Q", 41),
                symbols=(TextSymbol("_counter", 0),)),
    ])


def _unit(name, calls=None):
    return NativeObject.from_sections(
        [Section(
            "__text", "__TEXT", (_BL + _RET) if calls else _RET,
            align_log2=2, flags=TEXT_SECTION_FLAGS,
            symbols=(TextSymbol(name, 0),),
            relocations=() if calls is None else (
                Relocation(0, calls, spec.ARM64_RELOC_BRANCH26, True),
            ),
        )],
        undefined=() if calls is None else (calls,),
    )


def _archive(members):
    result = bytearray(b"!<arch>\n")
    for name, obj in members:
        payload = obj.to_macho()
        header = (
            (name + "/").ljust(16) + "0".ljust(12) + "0".ljust(6)
            + "0".ljust(6) + "644".ljust(8) + str(len(payload)).ljust(10)
            + "`\n"
        ).encode("ascii")
        if len(header) != 60:
            raise AssertionError("invalid test archive member name")
        result.extend(header)
        result.extend(payload)
        if len(payload) % 2:
            result.extend(b"\n")
    return bytes(result)


def _uleb(data, offset):
    value = 0
    shift = 0
    while True:
        if offset >= len(data) or shift > 63:
            raise AssertionError("malformed export ULEB")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
        shift += 7


def _exports(image):
    obj = spec.parse_object(image)
    commands = [row for row in obj.commands if row.cmd == spec.LC_DYLD_EXPORTS_TRIE]
    if not commands:
        return {}
    if len(commands) != 1:
        raise AssertionError("duplicate export-trie load command")
    start, size = struct.unpack_from("<II", commands[0].raw, 8)
    data = image[start:start + size]
    if len(data) != size or not size:
        raise AssertionError("export trie lies outside the image")
    result = {}
    pending = [(0, b"")]
    seen = set()
    while pending:
        offset, prefix = pending.pop()
        if offset in seen:
            raise AssertionError("export trie revisits a node")
        seen.add(offset)
        terminal_size, cursor = _uleb(data, offset)
        terminal_end = cursor + terminal_size
        if terminal_end >= len(data):
            raise AssertionError("export terminal exceeds the trie")
        if terminal_size:
            flags, cursor = _uleb(data, cursor)
            address, cursor = _uleb(data, cursor)
            if cursor != terminal_end or prefix in result:
                raise AssertionError("invalid export terminal")
            result[prefix.decode("ascii")] = (flags, address)
        cursor = terminal_end
        children = data[cursor]
        cursor += 1
        for _ in range(children):
            end = data.find(b"\0", cursor)
            if end <= cursor:
                raise AssertionError("invalid export edge")
            edge = data[cursor:end]
            child, cursor = _uleb(data, end + 1)
            pending.append((child, prefix + edge))
    return result


def _imports(image):
    obj = spec.parse_object(image)
    command = next(row for row in obj.commands if row.cmd == spec.LC_DYLD_CHAINED_FIXUPS)
    start, size = struct.unpack_from("<II", command.raw, 8)
    data = image[start:start + size]
    version, _, imports_start, strings_start, count, form, strings_form = (
        struct.unpack_from("<7I", data)
    )
    if (version, form, strings_form) != (0, 1, 0):
        raise AssertionError("unexpected chained-import format")
    result = {}
    for index in range(count):
        word = struct.unpack_from("<I", data, imports_start + 4 * index)[0]
        ordinal = word & 255
        if ordinal >= 128:
            ordinal -= 256
        weak = bool((word >> 8) & 1)
        offset = strings_start + (word >> 9)
        end = data.index(b"\0", offset)
        result[data[offset:end].decode("ascii")] = (ordinal, weak, index)
    return result


def _shared_object():
    return NativeObject.from_sections(
        [
            Section(
                "__text", "__TEXT", _BL + _BL + _RET, align_log2=2,
                flags=TEXT_SECTION_FLAGS,
                symbols=(TextSymbol("_extension_entry", 0),),
                relocations=(
                    Relocation(0, "_host_api", spec.ARM64_RELOC_BRANCH26, True),
                    Relocation(4, "_puts", spec.ARM64_RELOC_BRANCH26, True),
                ),
            ),
            Section(
                "__data", "__DATA", b"\0" * 16,
                symbols=(TextSymbol("_extension_data", 0),),
                relocations=(
                    Relocation(0, "_host_data", spec.ARM64_RELOC_UNSIGNED, False, 3),
                    Relocation(8, "_host_optional", spec.ARM64_RELOC_UNSIGNED, False, 3),
                ),
            ),
        ],
        undefined=("_host_api", "_host_data", "_host_optional", "_puts"),
        weak_undefined=("_host_optional",),
    )


def _shared_image(objects, **kwargs):
    with patch.object(macho_shared.platform, "system", return_value="Darwin"), \
            patch.object(macho_shared.platform, "machine", return_value="arm64"), \
            patch.object(macho_shared, "_libsystem_exports_symbol",
                         side_effect=lambda name: name == "_puts"), \
            patch.object(macho_exec, "_libsystem_exports_symbol",
                         side_effect=lambda name: name == "_puts"):
        return macho_shared.link_shared_library(
            objects, target=_TARGET, identity="extension-symbol-test", **kwargs,
        )


class ExtensionSymbolTests(unittest.TestCase):
    def assertSignature(self, image):
        signature = parse_signature(image)
        self.assertEqual(signature.dataoff + signature.datasize, len(image))
        # Check the actual page digests, not only signature structure.
        blob = image[signature.dataoff:]
        _, directory = struct.unpack_from(">II", blob, 12)
        hash_offset = struct.unpack_from(">I", blob, directory + 16)[0]
        count = struct.unpack_from(">I", blob, directory + 28)[0]
        limit = struct.unpack_from(">I", blob, directory + 32)[0]
        self.assertEqual(limit, signature.dataoff)
        for index in range(count):
            start = index * 4096
            expected = hashlib.sha256(image[start:min(start + 4096, limit)]).digest()
            offset = directory + hash_offset + 32 * index
            self.assertEqual(blob[offset:offset + 32], expected)
        return signature

    def test_exact_public_exports_have_correct_addresses_and_valid_signature(self):
        image = macho_exec.link_executable(
            [_object()], export_symbols=("_counter", "_callee"),
        )
        obj = spec.parse_object(image)
        self.assertEqual(obj.header["filetype"], spec.MH_EXECUTE)
        symbols = {row["name"]: row for row in obj.symbols()}
        self.assertEqual(_exports(image), {
            name: (0, symbols[name]["n_value"] - macho_exec.TEXT_BASE)
            for name in ("_counter", "_callee")
        })
        signature = self.assertSignature(image)
        command = next(row for row in obj.commands if row.cmd == spec.LC_DYLD_EXPORTS_TRIE)
        offset, size = struct.unpack_from("<II", command.raw, 8)
        self.assertLessEqual(offset + size, signature.dataoff)
        linkedit = next(row for row in obj.commands
                        if row.cmd == spec.LC_SEGMENT_64
                        and spec.SEGMENT_COMMAND_64.unpack(row.raw)["segname"].rstrip(b"\0") == b"__LINKEDIT")
        fields = spec.SEGMENT_COMMAND_64.unpack(linkedit.raw)
        self.assertEqual(fields["fileoff"] + fields["filesize"], len(image))
        text = next(row for row in obj.sections() if row["sectname_str"] == "__text")
        branch = struct.unpack_from("<I", image, text["offset"])[0]
        self.assertEqual(branch, 0x94000002)  # _main + 8 reaches _callee.
        self.assertEqual(
            macho_exec.link_executable([_object()], export_symbols=("_callee", "_counter")),
            image,
        )

    def test_archive_export_roots_pull_transitive_members_but_not_unrelated_members(self):
        archives = [
            _archive([("unrelated.o", _unit("_unrelated"))]),
            _archive([("helper.o", _unit("_helper")),
                      ("wanted.o", _unit("_wanted", "_helper")),
                      ("unused.o", _unit("_unused"))]),
        ]
        image = macho_exec.link_executable(
            [_object()], archives=archives, export_symbols=("_wanted",),
        )
        names = {row["name"] for row in spec.parse_object(image).symbols()}
        self.assertTrue({"_wanted", "_helper"}.issubset(names))
        self.assertTrue({"_unrelated", "_unused"}.isdisjoint(names))
        self.assertEqual(set(_exports(image)), {"_wanted"})
        prepared = macho_exec.prepare_executable_object(
            [_object()], archives=archives, required_symbols=("_wanted",),
        )
        self.assertEqual(
            macho_exec.link_prepared_executable(prepared, export_symbols=("_wanted",)),
            image,
        )
        self.assertSignature(image)

    def test_zerofill_export_uses_its_virtual_address_without_file_payload(self):
        obj = NativeObject.from_sections([
            Section("__text", "__TEXT", _RET, align_log2=2,
                    flags=TEXT_SECTION_FLAGS, symbols=(TextSymbol("_main", 0),)),
            Section("__bss", "__DATA", flags=spec.S_ZEROFILL, zerofill_size=16,
                    symbols=(TextSymbol("_zeroed", 0),)),
        ])
        image = macho_exec.link_executable([obj], export_symbols=("_zeroed",))
        parsed = spec.parse_object(image)
        section = next(row for row in parsed.sections() if row["sectname_str"] == "__bss")
        self.assertEqual(section["offset"], 0)
        self.assertEqual(section["size"], 16)
        self.assertEqual(_exports(image), {"_zeroed": (0, section["addr"] - macho_exec.TEXT_BASE)})
        self.assertSignature(image)

    def test_missing_or_nonpublic_exports_fail_before_publication(self):
        for name in ("_missing", "_local", "_private"):
            with self.subTest(name=name):
                with self.assertRaises(LinkError):
                    macho_exec.link_executable([_object()], export_symbols=(name,))
                prepared = macho_exec.prepare_executable_object([_object()])
                with self.assertRaises(LinkError):
                    macho_exec.link_prepared_executable(prepared, export_symbols=(name,))
        with self.assertRaises(LinkError):
            macho_exec.prepare_executable_object(
                [_object()], required_symbols=("_missing",),
            )

    def test_public_l_prefix_symbols_survive_direct_and_archive_merges(self):
        for name in ("LibraryApi", "library_api"):
            for from_archive in (False, True):
                with self.subTest(name=name, from_archive=from_archive):
                    objects = [_object()]
                    archives = []
                    if from_archive:
                        archives.append(_archive([("api.o", _unit(name))]))
                    else:
                        objects.append(_unit(name))
                    image = macho_exec.link_executable(
                        objects, archives=archives, export_symbols=(name,),
                    )
                    self.assertEqual(set(_exports(image)), {name})
                    self.assertSignature(image)
            # The generic classification fix also retains public L/l names
            # in ordinary links without an explicit export request.
            ordinary = macho_exec.prepare_executable_object([_object(), _unit(name)])
            self.assertIn(name, {symbol.name for symbol in ordinary.symbols})

    def test_unused_local_l_temporaries_are_still_pruned(self):
        objects = []
        for name in ("_main", "_other"):
            objects.append(NativeObject.from_sections([
                Section("__text", "__TEXT", _RET, align_log2=2,
                        flags=TEXT_SECTION_FLAGS,
                        symbols=(TextSymbol("Ltmp0", 0, external=False),
                                 TextSymbol(name, 0))),
            ]))
        merged = macho_exec.prepare_executable_object(objects)
        self.assertEqual({symbol.name for symbol in merged.symbols}, {"_main", "_other"})

    def test_duplicate_public_l_prefix_definitions_still_fail(self):
        for name in ("LibraryApi", "library_api"):
            with self.subTest(name=name):
                with self.assertRaises(LinkError):
                    macho_exec.prepare_executable_object(
                        [_object(), _unit(name), _unit(name)],
                    )

    def test_consuming_source_view_preserves_exports_and_releases_input_list(self):
        inputs = [_object()]
        image = macho_exec.link_executable(
            inputs, export_symbols=("_callee", "_counter"),
            _consume_inputs=True, _direct_source_view=True,
        )
        self.assertEqual(inputs, [])
        self.assertEqual(set(_exports(image)), {"_callee", "_counter"})
        self.assertEqual(
            image,
            macho_exec.link_executable([_object()], export_symbols=("_callee", "_counter")),
        )
        self.assertSignature(image)

    def test_duplicate_and_malformed_explicit_inventories_are_rejected(self):
        cases = (
            "_main", {"_main"}, b"_main", 1,
            ("_main", "_main"), ("",), ("nul\0name",), ("nonascii_é",), (1,),
        )
        for names in cases:
            with self.subTest(names=repr(names)):
                with self.assertRaises((LinkError, BackendUnavailable, ValueError)):
                    macho_exec.link_executable([_object()], export_symbols=names)
                with self.assertRaises((LinkError, BackendUnavailable, ValueError)):
                    macho_exec.prepare_executable_object([_object()], required_symbols=names)
                host_names = (
                    ("_unused_host_api", "_unused_host_api")
                    if names == ("_main", "_main") else names
                )
                with self.assertRaises((LinkError, BackendUnavailable, ValueError)):
                    _shared_image([_object()], host_import_symbols=host_names)

    def test_new_exports_do_not_enter_unqualified_semantic_layout(self):
        with self.assertRaises(LinkError):
            macho_exec.link_executable(
                [_object()], export_symbols=("_main",), semantic_manifest={},
            )
        with self.assertRaises(LinkError):
            macho_exec.prepare_executable_object(
                [_object()], required_symbols=("_main",), semantic_manifest={},
            )

    def test_empty_options_preserve_existing_api_images(self):
        default = macho_exec.link_executable([_object()])
        self.assertEqual(
            macho_exec.link_executable([_object()], export_symbols=()), default,
        )
        prepared = macho_exec.prepare_executable_object([_object()], required_symbols=())
        self.assertEqual(macho_exec.link_prepared_executable(prepared), default)
        self.assertEqual(
            macho_exec.link_prepared_executable(prepared, export_symbols=()), default,
        )
        self.assertEqual(_exports(default), {})
        self.assertEqual(
            _shared_image([_object()], host_import_symbols=()),
            _shared_image([_object()]),
        )

    def test_exports_do_not_bypass_compact_output_accounting(self):
        prepared = macho_exec.prepare_executable_object([_object()])
        with self.assertRaises(LinkError):
            macho_exec.link_prepared_executable(
                prepared, export_symbols=("_main",), compact_budget=object(),
            )
        with self.assertRaises(LinkError):
            macho_exec._prepare_executable_image(
                prepared, export_symbols=("_main",), _owned_output=object(),
            )

    def test_export_trie_handles_long_shared_prefixes_and_multibyte_offsets(self):
        names = ["_long_" + "x" * 280 + suffix for suffix in ("a", "b")]
        names.extend("_value_" + str(index).zfill(3) for index in range(40))
        obj = NativeObject.from_sections([
            Section(
                "__text", "__TEXT", _RET, align_log2=2, flags=TEXT_SECTION_FLAGS,
                symbols=tuple(TextSymbol(name, 0) for name in ["_main"] + names),
            ),
        ])
        image = macho_exec.link_executable([obj], export_symbols=names)
        self.assertEqual(set(_exports(image)), set(names))
        self.assertTrue(all(flags == 0 for flags, _ in _exports(image).values()))
        self.assertSignature(image)

    def test_shared_host_bindings_use_main_ordinal_for_function_data_and_weak_imports(self):
        inventory = ("_host_api", "_host_data", "_host_optional")
        image = _shared_image([_shared_object()], host_import_symbols=inventory)
        obj = spec.parse_object(image)
        self.assertEqual(obj.header["filetype"], spec.MH_DYLIB)
        self.assertFalse(any(row.cmd == spec.LC_MAIN for row in obj.commands))
        imports = _imports(image)
        self.assertEqual({name: row[:2] for name, row in imports.items()}, {
            "_host_api": (-1, False), "_host_data": (-1, False),
            "_host_optional": (-1, True), "_puts": (1, False),
        })
        symbols = {row["name"]: row for row in obj.symbols()}
        for name, (ordinal, weak, _) in imports.items():
            self.assertEqual((symbols[name]["n_desc"] >> 8) & 255, ordinal & 255)
            self.assertEqual(bool(symbols[name]["n_desc"] & spec.N_WEAK_REF), weak)
        data = next(row for row in obj.sections() if row["sectname_str"] == "__data")
        for offset, name in ((0, "_host_data"), (8, "_host_optional")):
            word = struct.unpack_from("<Q", image, data["offset"] + offset)[0]
            self.assertEqual(word >> 63, 1)
            self.assertEqual(word & 0xFFFFFF, imports[name][2])
        loaded = []
        for command in obj.commands:
            if command.cmd == spec.LC_LOAD_DYLIB:
                start = struct.unpack_from("<I", command.raw, 8)[0]
                loaded.append(command.raw[start:].split(b"\0", 1)[0])
        self.assertEqual(loaded, [b"/usr/lib/libSystem.B.dylib"])
        self.assertEqual(set(_exports(image)), {"_extension_entry", "_extension_data"})
        self.assertSignature(image)
        self.assertEqual(
            _shared_image([_shared_object()],
                          host_import_symbols=inventory + ("_unused_host_api",)),
            image,
        )

    def test_host_inventory_cannot_supply_a_local_definition(self):
        with self.assertRaises((LinkError, BackendUnavailable)):
            _shared_image([_object()], host_import_symbols=("_main",))

    def test_data_only_shared_anchor_avoids_host_inventory_reserved_names(self):
        reserved = ("__pcc_shared_link_entry", "__pcc_shared_link_entry_")
        obj = NativeObject.from_sections([
            Section("__data", "__DATA", struct.pack("<Q", 9),
                    symbols=(TextSymbol("_extension_data", 0),)),
        ])
        image = _shared_image([obj], host_import_symbols=reserved)
        parsed = spec.parse_object(image)
        definitions = {row["name"] for row in parsed.symbols()
                       if row["n_type"] & spec.N_TYPE == spec.N_SECT}
        self.assertTrue(set(reserved).isdisjoint(definitions))
        self.assertEqual(_imports(image), {})
        self.assertEqual(set(_exports(image)), {"_extension_data"})
        self.assertSignature(image)

    def test_missing_host_inventory_does_not_get_a_prefix_based_fallback(self):
        with self.assertRaises((LinkError, BackendUnavailable)):
            _shared_image([_shared_object()], host_import_symbols=("_host_data",))
        with self.assertRaises((LinkError, BackendUnavailable)):
            _shared_image([_unit("_entry", "_PyLong_FromLong")])
        with patch.object(macho_exec, "_libsystem_exports_symbol", return_value=False):
            with self.assertRaises(LinkError):
                macho_exec.link_executable([_unit("_main", "_host_api")])
        with self.assertRaises(TypeError):
            macho_exec.link_executable(
                [_unit("_main", "_host_api")], host_import_symbols=("_host_api",),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

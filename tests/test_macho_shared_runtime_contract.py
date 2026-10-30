"""Owned shared-link admission/import contracts, independent of the host OS.

These host unit tests use real native-object records. Platform and resolver
doubles exercise dispatch and error propagation; they do not execute a native
compiler, load a dylib, or qualify the extension/runtime GC boundary.
"""

from __future__ import annotations

import builtins
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from pcc.backend import BackendUnavailable, macho_exec, macho_shared
from pcc.backend.macho_link import LinkError
from pcc.backend.macho_obj import Section, TextSymbol, TEXT_SECTION_FLAGS
from pcc.backend.native_object import NativeObject


_TARGET = "aarch64-apple-darwin"


def _object(*, undefined=(), weak_undefined=()):
    return NativeObject.from_sections(
        [
            Section(
                "__text",
                "__TEXT",
                b"\xc0\x03\x5f\xd6",
                align_log2=2,
                flags=TEXT_SECTION_FLAGS,
                symbols=(
                    TextSymbol("_local", 0, external=False),
                    TextSymbol("_private", 0, private_external=True),
                    TextSymbol("_entry", 0),
                ),
            ),
        ],
        undefined=undefined,
        weak_undefined=weak_undefined,
    )


class SharedRuntimeContractTests(unittest.TestCase):
    def _validate(self, implementation, system="Darwin", machine="arm64",
                  target=_TARGET):
        host = SimpleNamespace(
            implementation=SimpleNamespace(name=implementation),
        )
        with patch.object(macho_shared, "sys", host), \
                patch.object(macho_shared.platform, "system", return_value=system), \
                patch.object(macho_shared.platform, "machine", return_value=machine):
            return macho_shared.validate_target(target)

    def test_pcc_native_darwin_host_is_admitted(self):
        self.assertIsNone(self._validate("pcc"))

    def test_cpython_darwin_host_remains_admitted(self):
        self.assertIsNone(self._validate("cpython"))

    def test_other_interpreter_is_rejected(self):
        with self.assertRaises(BackendUnavailable):
            self._validate("pypy")

    def test_host_and_target_limits_remain_fail_closed(self):
        cases = (
            ("Linux", "arm64", _TARGET),
            ("Darwin", "x86_64", _TARGET),
            ("Darwin", "arm64", "aarch64-unknown-linux-gnu"),
            ("Darwin", "arm64", "x86_64-apple-darwin"),
            ("Darwin", "arm64", "unknown"),
        )
        for implementation in ("cpython", "pcc"):
            for system, machine, target in cases:
                with self.subTest(implementation=implementation, system=system,
                                  machine=machine, target=target):
                    with self.assertRaises(BackendUnavailable):
                        self._validate(implementation, system, machine, target)

    def test_shared_module_import_does_not_require_ctypes(self):
        original_import = builtins.__import__

        def reject_ctypes(name, *args, **kwargs):
            if name == "ctypes" or name.startswith("ctypes."):
                raise AssertionError("shared linker imported the host FFI")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", reject_ctypes):
            importlib.reload(macho_shared)

    def test_defined_and_weak_symbols_do_not_query_provider(self):
        merged = _object(undefined=("_optional",), weak_undefined=("_optional",))
        resolver = Mock(side_effect=AssertionError("unexpected provider query"))
        with patch.object(macho_shared, "_libsystem_exports_symbol", resolver):
            self.assertIsNone(macho_shared._check_imports(merged))
        resolver.assert_not_called()

    def test_strong_imports_use_existing_owned_provider_without_renaming(self):
        self.assertIs(macho_shared._libsystem_exports_symbol,
                      macho_exec._libsystem_exports_symbol)
        merged = _object(
            undefined=("_puts", "malloc", "_optional"),
            weak_undefined=("_optional",),
        )
        resolver = Mock(return_value=True)
        with patch.object(macho_shared, "_libsystem_exports_symbol", resolver):
            self.assertIsNone(macho_shared._check_imports(merged))
        self.assertEqual(resolver.call_args_list, [call("_puts"), call("malloc")])

    def test_missing_strong_symbol_still_rejected(self):
        for name in ("_missing_provider", "_PyLong_FromLong"):
            with self.subTest(name=name):
                merged = _object(undefined=(name,))
                resolver = Mock(return_value=False)
                with patch.object(macho_shared, "_libsystem_exports_symbol", resolver):
                    with self.assertRaises(BackendUnavailable) as caught:
                        macho_shared._check_imports(merged)
                self.assertIn("unsupported libSystem import: " + name,
                              str(caught.exception))
                resolver.assert_called_once_with(name)

    def test_provider_memory_error_is_not_swallowed(self):
        merged = _object(undefined=("_puts",))
        error = MemoryError("symbol name allocation failed")
        with patch.object(macho_shared, "_libsystem_exports_symbol",
                          side_effect=error):
            with self.assertRaises(MemoryError) as caught:
                macho_shared._check_imports(merged)
        self.assertIs(caught.exception, error)

    def test_provider_link_error_is_not_swallowed(self):
        merged = _object(undefined=("_puts",))
        error = LinkError("target library unavailable")
        with patch.object(macho_shared, "_libsystem_exports_symbol",
                          side_effect=error):
            with self.assertRaises(LinkError) as caught:
                macho_shared._check_imports(merged)
        self.assertIs(caught.exception, error)

    def test_link_arguments_do_not_gain_an_external_provider(self):
        self.assertIsNone(
            macho_shared.validate_link_args(("-lSystem", "-lc", "-lm",
                                              "-lpthread", "-ldl"))
        )
        for argument in ("-lpython3.15", "-lmissing", "-undefined",
                         "dynamic_lookup", "/tmp/extra-runtime.a"):
            with self.subTest(argument=argument):
                with self.assertRaises(BackendUnavailable):
                    macho_shared.validate_link_args((argument,))


if __name__ == "__main__":
    unittest.main(verbosity=2)

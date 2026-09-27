"""Windows path contracts through the supplied native compiler.

The host only creates fixture directories/reparse points. Every path operation
under assertion executes in the emitted program, without host Python fallback.
"""

import os
import struct

import pytest

from tests.integration.test_owned_platform_native import compile_run, native_compiler

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.name != "nt", reason="Windows path ABI")]


def test_native_windows_component_paths(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r'''
        import os
        from pathlib import Path
        def check():
            assert os.path.commonpath(["C:/Work/./Alpha", "c:\\work\\Beta"]) == "C:\\Work"
            assert os.path.commonpath(["C:Work/Alpha", "c:work/Beta"]) == "C:Work"
            assert os.path.commonpath(["C:/Work/../Alpha", "c:/work/../Beta"]) == "C:\\Work\\.."
            assert os.path.commonpath(["C:/One", "c:/Two"]) == "C:\\"
            assert os.path.commonpath(["one", "two"]) == ""
            assert os.path.commonpath(["C:/Ä/Alpha", "c:/ä/Beta"]) == "C:\\Ä"
            assert os.path.commonpath(["//HOST/Share/Folder/One", "\\\\host\\share\\folder\\Two"]) == "\\\\HOST\\Share\\Folder"
            assert os.path.relpath("C:/Work/Alpha", "c:\\work\\Beta") == "..\\Alpha"
            assert os.path.relpath("C:/Ä/Alpha", "c:/ä") == "Alpha"
            assert os.path.relpath("//HOST/Share/One", "\\\\host\\share\\Two") == "..\\One"
            assert os.path.relpath("C:/work", "c:/WORK") == "."
            assert os.path.normpath("C:") == "C:"
            assert os.path.normpath("\\\\host\\share") == "\\\\host\\share"
            assert os.path.splitdrive("\\\\?\\C:\\work")[0] == "\\\\?\\C:"
            assert os.path.normpath("\\\\?\\C:\\work\\..\\file") == "\\\\?\\C:\\file"
            assert os.path.splitext("C:\\dir.ext\\.hidden") == ("C:\\dir.ext\\.hidden", "")
            assert os.path.splitext("C:\\dir.ext\\...hidden") == ("C:\\dir.ext\\...hidden", "")
            assert os.path.splitext("C:\\dir.ext\\file.txt") == ("C:\\dir.ext\\file", ".txt")
            assert str(Path("C:/Ä/Child").relative_to("c:/ä")) == "Child"
            assert str(Path("C:child").relative_to("c:")) == "child"
            assert str(Path("one/two").relative_to(".")) == "one\\two"
            count = 0
            try:
                os.path.commonpath(["C:/one", "D:/two"])
            except ValueError:
                count += 1
            try:
                os.path.commonpath(["C:/one", "C:two"])
            except ValueError:
                count += 1
            try:
                os.path.commonpath([])
            except ValueError:
                count += 1
            try:
                os.path.relpath("C:/one", "D:/two")
            except ValueError:
                count += 1
            try:
                os.path.relpath("", "C:/one")
            except ValueError:
                count += 1
            try:
                Path("C:/OneMore/file").relative_to("c:/one")
            except ValueError:
                count += 1
            assert count == 6
            print("windows components ok")
        check()
    ''', expected="windows components ok\n")


def test_native_windows_environment_path_expansion(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r'''
        import os
        def check():
            os.environ["PCC_PATH_VALUE"] = "folder"
            os.environ["PCC-PATH-VALUE"] = "hyphen"
            assert os.path.expandvars("%pcc_path_value%/${PCC_PATH_VALUE}/$PCC_PATH_VALUE") == "folder/folder/folder"
            assert os.path.expandvars("%PCC_PATH_VALUE%%PCC_PATH_VALUE%") == "folderfolder"
            assert os.path.expandvars("%%PCC_PATH_VALUE%%") == "%PCC_PATH_VALUE%"
            assert os.path.expandvars("$$PCC_PATH_VALUE") == "$PCC_PATH_VALUE"
            assert os.path.expandvars("'$PCC_PATH_VALUE'/%PCC_PATH_VALUE%") == "'$PCC_PATH_VALUE'/folder"
            assert os.path.expandvars("'$PCC_PATH_VALUE") == "'$PCC_PATH_VALUE"
            assert os.path.expandvars("${PCC_PATH_VALUE/$PCC_PATH_VALUE") == "${PCC_PATH_VALUE/$PCC_PATH_VALUE"
            assert os.path.expandvars("%PCC_PATH_VALUE/$PCC_PATH_VALUE") == "%PCC_PATH_VALUE/$PCC_PATH_VALUE"
            assert os.path.expandvars("$PCC-PATH-VALUE") == "hyphen"
            assert os.path.expandvars("${PCC_PATH_MISSING}/%PCC_PATH_MISSING%") == "${PCC_PATH_MISSING}/%PCC_PATH_MISSING%"
            assert os.path.expanduser("~/child") == "C:\\Users\\Alice/child"
            assert os.path.expanduser("~Alice\\child") == "C:\\Users\\Alice\\child"
            assert os.path.expanduser("~Bob\\child") == "C:\\Users\\Bob\\child"
            print("windows expansion ok")
        check()
    ''', expected="windows expansion ok\n", extra_env={"USERPROFILE": "C:\\Users\\Alice", "USERNAME": "Alice"})


def test_native_windows_home_drive_fallback(native_compiler, tmp_path, monkeypatch):
    monkeypatch.delenv("USERPROFILE", raising=False)
    compile_run(native_compiler, tmp_path, r'''
        import os
        def check():
            assert os.path.expanduser("~") == "D:\\Homes\\Alice"
            assert os.path.expanduser("~Bob/child") == "D:\\Homes\\Bob/child"
            print("windows home fallback ok")
        check()
    ''', expected="windows home fallback ok\n", extra_env={
        "HOMEDRIVE": "D:", "HOMEPATH": "\\Homes\\Alice", "USERNAME": "Alice", "HOME": "ignored-on-windows",
    })


def test_native_windows_makedirs_preserves_drive_root(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path / "build", r'''
        import os
        import sys
        def check():
            drive = os.path.splitdrive(os.path.abspath(sys.argv[1]))[0]
            root = drive + "\\"
            os.makedirs(root, exist_ok=True)
            try:
                os.makedirs(root)
            except OSError:
                pass
            else:
                raise AssertionError("existing root was silently accepted")
            child = os.path.join(sys.argv[1], "nested", "leaf")
            os.makedirs(child)
            assert os.path.isdir(child)
            print("directories ok")
        check()
    ''', argv=[str(tmp_path)], expected="directories ok\n")


def _create_directory_junction(link, target):
    """Create an NTFS junction without shelling out or requiring symlink privilege."""
    import ctypes
    from ctypes import wintypes

    link.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    control = kernel.DeviceIoControl
    control.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    control.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    target_text = str(target.absolute())
    substitute = ("\\??\\" + target_text).encode("utf-16-le")
    printed = target_text.encode("utf-16-le")
    payload = substitute + b"\0\0" + printed + b"\0\0"
    reparse = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(payload), 0,
                          0, len(substitute), len(substitute) + 2, len(printed)) + payload
    handle = create_file(str(link), 0x40000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        data = ctypes.create_string_buffer(reparse)
        returned = wintypes.DWORD()
        if not control(handle, 0x000900A4, data, len(reparse), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


@pytest.mark.parametrize("missing_target", [False, True])
def test_native_windows_realpath_resolves_ancestors(native_compiler, tmp_path, missing_target):
    target = tmp_path / "actual 中文"
    if not missing_target:
        (target / "existing").mkdir(parents=True)
    link = tmp_path / "alias"
    _create_directory_junction(link, target)
    query = link / "existing" / "missing" / "leaf.txt"
    expected = target / "existing" / "missing" / "leaf.txt"
    try:
        compile_run(native_compiler, tmp_path / "build", r'''
            import os
            import sys
            from pathlib import Path
            def check():
                query = sys.argv[1]
                expected = sys.argv[2]
                assert os.path.normcase(os.path.realpath(query)) == os.path.normcase(expected)
                assert os.path.normcase(str(Path(query).resolve())) == os.path.normcase(expected)
                assert os.path.abspath("") == os.getcwd()
                print("windows realpath ok")
            check()
        ''', argv=[str(query), str(expected)], expected="windows realpath ok\n")
    finally:
        # Removing a junction does not walk into its target.
        link.rmdir()

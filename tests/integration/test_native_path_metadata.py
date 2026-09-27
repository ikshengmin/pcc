"""Execute real metadata shapes with an explicitly supplied native compiler.

PCC_PATH_METADATA_COMPILER must identify the candidate pcc1/pcc2, and
PCC_RUNTIME_ARCHIVE its matching runtime. Run under the ordinary test-tree
watchdog/RSS cap; this gate never bootstraps or selects a host compiler.
"""

import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.integration


def _compile(tmp_path, source):
    compiler = os.environ.get("PCC_PATH_METADATA_COMPILER", "")
    runtime = os.environ.get("PCC_RUNTIME_ARCHIVE", "")
    assert compiler and Path(compiler).is_file(), "set PCC_PATH_METADATA_COMPILER"
    assert runtime and Path(runtime).is_file(), "set matching PCC_RUNTIME_ARCHIVE"
    program = tmp_path / "metadata.py"
    program.write_text(source, encoding="utf-8")
    output = tmp_path / ("metadata.exe" if os.name == "nt" else "metadata")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.update(PCC_HOST_PYTHON=str(tmp_path / "forbidden-python"),
               PCC_RUNTIME_CC=str(tmp_path / "forbidden-cc"), PCC_SELF_LINK="pcc")
    compiled = subprocess.run([compiler, "--backend", "self", str(program), "-o", str(output)],
                              env=env, text=True, capture_output=True, timeout=120)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert output.is_file()
    return output, env


def _sparse_file(path, size):
    with path.open("wb") as stream:
        if os.name == "nt":
            # NTFS requires the sparse attribute before extending EOF; avoid
            # physically allocating the test's 64 GiB on Windows.
            import ctypes
            import msvcrt
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            ioctl = api.DeviceIoControl
            ioctl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                              ctypes.c_void_p, ctypes.c_void_p]
            ioctl.restype = ctypes.c_int
            returned = ctypes.c_uint32()
            assert ioctl(msvcrt.get_osfhandle(stream.fileno()), 0x900C4,
                         None, 0, None, 0, ctypes.byref(returned), None), ctypes.get_last_error()
        stream.truncate(size)


def test_native_symlinks_and_large_sparse_size(tmp_path):
    regular = tmp_path / "regular-中文"
    regular.write_bytes(b"nonzero\x00bytes")
    live = tmp_path / "live-link"
    dangling = tmp_path / "dangling-link"
    live.symlink_to(regular)
    dangling.symlink_to(tmp_path / "absent-target")
    sparse = tmp_path / "huge-sparse"
    logical_size = 64 * 1024**3 + 123
    _sparse_file(sparse, logical_size)
    source = f'''import os
def main():
    assert os.path.islink({str(live)!r})
    assert os.path.islink({str(dangling)!r})
    assert not os.path.islink({str(regular)!r})
    assert not os.path.islink({str(tmp_path)!r})
    assert not os.path.islink({str(tmp_path / "missing")!r})
    assert os.path.getsize({str(live)!r}) == 13
    assert os.path.getsize({str(sparse)!r}) == {logical_size}
    for path in [{str(dangling)!r}, {str(tmp_path / "missing")!r}]:
        try:
            os.path.getsize(path)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing path accepted")
    try:
        os.path.getsize({str(regular / "child")!r})
    except OSError:
        pass
    else:
        raise AssertionError("non-directory component accepted")
    assert not os.path.islink("bad\\x00path")
    try:
        os.path.getsize("bad\\x00path")
    except ValueError:
        pass
    else:
        raise AssertionError("embedded NUL accepted")
    print("metadata-ok")
main()
'''
    output, env = _compile(tmp_path, source)
    for gc in range(5):
        result = subprocess.run([str(output)], env=dict(env, PCC_GC_BACKEND=str(gc)),
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "metadata-ok\n"


@pytest.mark.pcc_gate(unavailable="POSIX FIFO metadata" if os.name == "nt" else None)
def test_native_getsize_does_not_open_a_fifo(tmp_path):
    fifo = tmp_path / "unopened-fifo"
    os.mkfifo(fifo)
    output, env = _compile(tmp_path, f'''import os
def main():
    print(os.path.getsize({str(fifo)!r}))
main()
''')
    result = subprocess.run([str(output)], env=env, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "0\n"


def test_native_getmtime_failure_and_pre_epoch_values(tmp_path):
    epoch = tmp_path / "epoch-zero"
    before_epoch = tmp_path / "before-epoch"
    ordinary = tmp_path / "ordinary"
    for path in (epoch, before_epoch, ordinary):
        path.write_bytes(b"metadata")
    os.utime(epoch, ns=(0, 0))
    negative_ns = -123456789 * 1_000_000_000
    os.utime(before_epoch, ns=(negative_ns, negative_ns))
    # The filesystem oracle is explicit: inability to represent a pre-epoch
    # timestamp must not accidentally turn the native assertion into a no-op.
    assert os.path.getmtime(epoch) == 0.0
    assert os.path.getmtime(before_epoch) == -123456789.0
    missing = tmp_path / "missing"
    nondirectory = ordinary / "child"
    output, env = _compile(tmp_path, f'''import os

def main():
    assert os.path.getmtime({str(epoch)!r}) == 0.0
    assert os.path.getmtime({str(before_epoch)!r}) == -123456789.0
    for path in [{str(missing)!r}, {str(nondirectory)!r}]:
        try:
            os.path.getmtime(path)
        except OSError:
            pass
        else:
            raise AssertionError("metadata failure accepted as timestamp")
    try:
        os.path.getmtime("bad\\x00path")
    except ValueError:
        pass
    else:
        raise AssertionError("embedded NUL accepted")
    print("mtime-ok")
main()
''')
    for gc in range(5):
        result = subprocess.run([str(output)], env=dict(env, PCC_GC_BACKEND=str(gc)),
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "mtime-ok\n"

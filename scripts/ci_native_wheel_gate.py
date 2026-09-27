"""Install one locally built platform wheel and execute its native compiler.

The wheel build itself is a separate CI step. A platform without a complete
native pcc1 must fail that step; this gate never substitutes the host launcher.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv
import zipfile


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def _installed_command(venv_root: Path, name: str) -> Path:
    bin_dir = venv_root / ("Scripts" if os.name == "nt" else "bin")
    candidates = (
        [bin_dir / f"{name}.exe", bin_dir / name]
        if os.name == "nt"
        else [bin_dir / name]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"installed wheel has no {name} command in {bin_dir}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_native_wheel_gate.py WHEEL_DIRECTORY")
    wheels = sorted(Path(sys.argv[1]).resolve().glob("python_cc-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one python-cc wheel, found {wheels!r}")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        wheel_metadata = [name for name in names if name.endswith(".dist-info/WHEEL")]
        if len(wheel_metadata) != 1:
            raise RuntimeError("wheel must contain exactly one WHEEL metadata file")
        tags = [
            line.removeprefix("Tag: ")
            for line in archive.read(wheel_metadata[0]).decode("utf-8").splitlines()
            if line.startswith("Tag: ")
        ]
    if not tags:
        raise RuntimeError("wheel metadata has no compatibility tag")
    if sys.platform.startswith("linux") and not any(
        tag.rsplit("-", 1)[-1].startswith(("manylinux", "musllinux")) for tag in tags
    ):
        raise RuntimeError(
            f"Linux wheel has no portable manylinux/musllinux tag: {tags!r}"
        )
    if not any(name.endswith((".data/scripts/pcc1", ".data/scripts/pcc1.exe")) for name in names):
        raise RuntimeError("wheel does not contain its native pcc1 script")
    if "pcc/py_runtime/libpy_runtime_pcc_py.a" not in names:
        raise RuntimeError("wheel does not contain the matching native runtime")

    with wheel.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(
        f"wheel={wheel.name} bytes={wheel.stat().st_size} sha256={digest}", flush=True
    )
    with TemporaryDirectory(prefix="pcc-native-wheel-ci-") as temp:
        root = Path(temp)
        venv_root = root / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_root)
        python = _installed_command(venv_root, "python")
        environment = os.environ.copy()
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel),
            ],
            cwd=root,
            env=environment,
            timeout=180,
        )
        host = _installed_command(venv_root, "pcc")
        native = _installed_command(venv_root, "pcc1")
        _run([str(host), "--help"], cwd=root, env=environment, timeout=30)
        _run([str(native), "--help"], cwd=root, env=environment, timeout=30)

        source = root / "main.py"
        source.write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n\n"
            "print(add(20, 22))\n",
            encoding="utf-8",
        )
        output = root / ("program.exe" if os.name == "nt" else "program")
        environment["PCC_HOST_PYTHON"] = str(root / "no-host-python")
        environment["PCC_RUNTIME_CC"] = str(root / "no-host-compiler")
        _run(
            [str(native), str(source), "-o", str(output)],
            cwd=root,
            env=environment,
            timeout=600,
        )
        if not output.is_file():
            raise RuntimeError(
                "installed pcc1 returned success without a native output"
            )
        stdout = _run([str(output)], cwd=root, env=environment, timeout=30)
        if stdout != "42\n":
            raise RuntimeError(f"native program printed {stdout!r}, expected '42\\n'")
    print("installed pcc and pcc1: native compile and execution passed", flush=True)


if __name__ == "__main__":
    main()

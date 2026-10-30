"""Versioned project fixtures, explicit oracle, fail-closed Linux/Windows route."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from tests.cpp.owned_execution import Execution

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects/tinyxml2-11.0.0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ProjectFixture:
    name: str
    revision: str
    standard: str
    files: tuple[Path, ...]
    translation_units: tuple[str, ...]
    driver: Path
    expected: Execution

    def verify(self) -> None:
        for path in (*self.files, self.driver):
            assert path.is_file(), f"missing project input: {path}"


def load_project(directory: Path = PROJECT) -> ProjectFixture:
    configuration = json.loads(
        (directory / "pcc-fixture.json").read_text(encoding="utf-8")
    )
    units = tuple(configuration["translation_units"])
    names = (*units, *configuration["headers"], configuration["license_file"])
    if not units or len(set(names)) != len(names):
        raise ValueError("project needs distinct source/header/license inputs")
    files = []
    for name in names:
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("project fixture paths must be plain basenames")
        path = directory / name
        if path.resolve().parent != directory.resolve():
            raise ValueError("project fixture symlink escapes its directory")
        files.append(path)
    driver = ROOT / configuration["driver"]
    if not driver.resolve().is_relative_to(ROOT):
        raise ValueError("project driver escapes the repository")
    expected = configuration["expected"]
    fixture = ProjectFixture(
        configuration["name"],
        configuration["revision"],
        configuration["standard"],
        tuple(files),
        units,
        driver,
        Execution(
            expected["returncode"],
            expected["stdout"].encode("utf-8"),
            expected["stderr"].encode("utf-8"),
        ),
    )
    fixture.verify()
    return fixture


def materialize_project(fixture: ProjectFixture, directory: Path) -> tuple[Path, ...]:
    fixture.verify()
    directory.mkdir()
    for path in fixture.files:
        (directory / path.name).write_bytes(path.read_bytes())
    driver = directory / "pcc_test_main.cpp"
    driver.write_bytes(fixture.driver.read_bytes())
    return (*(directory / name for name in fixture.translation_units), driver)


def compile_project_oracle(
    fixture: ProjectFixture, root: Path, *, compiler: str
) -> Path:
    """Explicit external reference, never called by the owned/native adapter."""
    units = materialize_project(fixture, root / "source")
    binary = root / "reference"
    command = [
        compiler,
        "-std=" + fixture.standard,
        "-pedantic-errors",
        "-O0",
        *(str(path) for path in units),
        "-o",
        str(binary),
    ]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=False, timeout=60
    )
    (root / "reference.stdout").write_bytes(result.stdout)
    (root / "reference.stderr").write_bytes(result.stderr)
    assert result.returncode == 0, (
        f"external C++17 project oracle failed ({result.returncode}): "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    fixture.verify()
    return binary


def native_project_environment(root: Path) -> dict[str, str]:
    """Do not inherit a host compiler route or rebuild an unspecified runtime."""
    environment = dict(os.environ)
    empty_path = root / "empty-path"
    empty_path.mkdir()
    forbidden = str(root / "forbidden-external-tool")
    environment.update(
        {
            "PATH": str(empty_path),
            "CC": forbidden,
            "CXX": forbidden,
            "PCC_HOST_PYTHON": forbidden,
            "PCC_HOST_PCC": forbidden,
            "PCC_RUNTIME_CC": "pcc",
            "PCC_RUNTIME_HIGH": "py",
            "PCC_SELF_LINK": "pcc",
            "PCC_SELF_OBJ": "pcc",
            "PCC_IR_TO_OBJ_EMITTER": "pcc",
            "PCC_NO_AUTO_PCC1": "1",
            "PCC_PY_FRONTEND_IR_CACHE": "0",
            "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
            "PCC_DEFER_FRONTEND_CODEGEN": "0",
        }
    )
    return environment


def compile_project_native(compiler: Path, fixture: ProjectFixture, root: Path) -> Path:
    """Exercise the Linux/Windows directory/separate-TU public route used for C projects.

    No missing compiler/runtime/capability is converted to skip or to an oracle
    invocation. C++17 is the intended default C++ language contract. This gate
    remains unsatisfied until the public native C++ directory route exists.
    """
    from scripts.bootstrap_platform import dependency_receipt
    from scripts.platform_process_watchdog import run
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    assert compiler.is_file(), f"native compiler missing: {compiler}"
    assert compiler.read_bytes().startswith(
        (b"\x7fELF", b"MZ")
    ), "native compiler must be an executable artifact, not a shell/Python wrapper"
    # A shell/Python wrapper or dynamically Python/LLVM-linked executable is not
    # accepted as the supplied native compiler. Static source ownership still
    # needs the compiler's build receipt; this header check alone is narrower.
    assert (
        sys.platform.startswith("linux") or sys.platform == "win32"
    ), "native C++ project adapter covers Linux/Windows ELF/PE only, not Darwin"
    compiler_shape = dependency_receipt(compiler)
    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE", "")
    assert (
        explicit
    ), "provide a source-matching PCC_RUNTIME_ARCHIVE; no implicit rebuild"
    archive, _manifest = _verified_test_runtime_archive(explicit)
    materialize_project(fixture, root / "source")
    environment = native_project_environment(root)
    environment["PCC_RUNTIME_ARCHIVE"] = str(archive)
    output = root / "native-project"
    command = [
        str(compiler),
        "--backend",
        "self",
        "--separate-tus",
        "--no-cache",
        "-O0",
        str(root / "source"),
        "-o",
        str(output),
    ]
    receipt = {
        "owner": "native-pcc1",
        "platform_scope": "linux-windows-elf-pe",
        "project": fixture.name,
        "revision": fixture.revision,
        "compiler": str(compiler),
        "compiler_sha256": sha256(compiler),
        "compiler_shape": compiler_shape,
        "runtime_sha256": sha256(archive),
        "command": command,
        "status": "compile-not-completed",
    }
    try:
        receipt["compile"] = run(
            command,
            cwd=root,
            env=environment,
            log_path=root / "native-compile.log",
            timeout=300,
            rss_limit=4 * 1024**3,
            native=True,
        )
        assert (
            output.is_file()
        ), "native C++ compiler returned success without an executable"
        receipt["output_shape"] = dependency_receipt(output)
        receipt["output_sha256"] = sha256(output)
        receipt["status"] = "compiled-not-executed"
    except Exception as error:
        receipt["error"] = repr(error)
        if isinstance(error, subprocess.CalledProcessError):
            receipt["returncode"] = error.returncode
        raise
    finally:
        receipt["compiler_unchanged"] = sha256(compiler) == receipt["compiler_sha256"]
        receipt["runtime_unchanged"] = sha256(archive) == receipt["runtime_sha256"]
        (root / "native-compile-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        fixture.verify()
    assert receipt["compiler_unchanged"] and receipt["runtime_unchanged"]
    return output

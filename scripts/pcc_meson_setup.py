"""Run an independently provided Meson 1.9.2 with the PCC C/C++ toolchain.

This is a host-development build-system adapter, not part of the compiler,
not a native-pcc1 qualification and not a sandbox for untrusted projects.
It requires a trusted Meson source distribution, project and PCC runtime.
Use a fresh out-of-source directory; no third-party source file is edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
HOST_TOOLS = ("git", "sh", "uname", "ninja")


def _digest(path: Path) -> str:
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def dispatch(scope: Path, argv: list[str]) -> int:
    """Called in a private process initially, and on Meson backend re-entry."""
    sys.dont_write_bytecode = True
    config = json.loads((scope / "config.json").read_text(encoding="utf-8"))
    runtime = Path(config["runtime"])
    if _digest(runtime) != config["runtime_sha256"]:
        raise ValueError("configured PCC runtime bytes changed; configure a fresh build directory")
    meson = Path(config["meson"])
    # Compilers, archivers and binary runners are absolute; no host cc, clang,
    # gcc, libc, pkg-config or native compiler discovery is a fallback.
    os.environ["PATH"] = str(scope / "host-tools")
    os.environ["PCC_RUNTIME_ARCHIVE"] = config["runtime"]
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PCC_REPO_ROOT"] = os.environ["PCC_SOURCE_ROOT"] = str(ROOT)
    if argv[:1] == ["--tool"]:
        # A Ninja tool line (including in another shell) binds the same explicit
        # archive *and its bytes* before strict production source admission.
        from pcc.tools.meson_cc import run
        if len(argv) < 2:
            raise ValueError("missing configured tool")
        return run(argv[1], argv[2:])
    commands = {tool: [sys.executable, str(scope / "launcher.py"), "--tool", tool]
                for tool in ("c", "cpp", "ar")}
    for key, tool in (("CC", "c"), ("CXX", "cpp"), ("AR", "ar")):
        os.environ[key] = os.environ[key + "_FOR_BUILD"] = shlex.join(commands[tool])
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT), str(meson)]
    from mesonbuild import mesonmain
    from pcc_meson_compilers import activate
    if Path(mesonmain.__file__).resolve() != meson / "mesonbuild/mesonmain.py":
        raise ValueError("Meson provider differs from the selected source directory")
    with activate(commands):
        return mesonmain.run(argv, str(scope / "launcher.py"))


def checked_dispatch(scope: Path, argv: list[str]) -> int:
    try:
        return dispatch(scope, argv)
    except Exception as err:
        print(f"pcc-meson: {err}", file=sys.stderr)
        return 2


def prepare(source: Path, build: Path, meson: Path, *, config_only: bool = False) -> int:
    source, build, meson = source.resolve(), build.resolve(), meson.resolve()
    if not (source / "meson.build").is_file() or not (meson / "mesonbuild/mesonmain.py").is_file():
        raise ValueError("expected a project meson.build and a trusted Meson source tree")
    if source in (build, *build.parents) or build in source.parents:
        raise ValueError("PCC Meson requires a separate out-of-source directory")
    runtime = os.environ.get("PCC_RUNTIME_ARCHIVE")
    if not runtime or not Path(runtime).is_file():
        raise ValueError("set PCC_RUNTIME_ARCHIVE to a current explicitly built PCC runtime")
    if build.exists() and (not build.is_dir() or any(build.iterdir())):
        raise ValueError("PCC Meson requires a fresh empty build directory")
    build.mkdir(parents=True, exist_ok=True)
    scope = build / "pcc-meson-toolchain"
    scope.mkdir(); host = scope / "host-tools"; host.mkdir()
    (host / "python3").symlink_to(sys.executable)
    for name in HOST_TOOLS:
        command = shutil.which(name)
        if command is not None:
            (host / name).symlink_to(Path(command).resolve())
    archive = Path(runtime).resolve()
    (scope / "config.json").write_text(json.dumps({"meson": str(meson), "runtime": str(archive),
                                                "runtime_sha256": _digest(archive)}) + "\n", encoding="utf-8")
    # Meson saves the launcher for backend subprocesses. They must re-enter the
    # same adapter instead of rediscovering a host compiler on a later run.
    (scope / "launcher.py").write_text(
        "from pathlib import Path\nimport sys\n"
        + "sys.path[:0] = " + repr([str(ROOT / "scripts"), str(ROOT)]) + "\n"
        + "from pcc_meson_setup import checked_dispatch\n"
        + "raise SystemExit(checked_dispatch(Path(__file__).resolve().parent, sys.argv[1:]))\n", encoding="utf-8")
    args = ["setup", str(build), str(source), "--wrap-mode=nodownload"]
    if config_only:
        # Stock Meson's 'none' backend is for real configuration/installed
        # data only. It refuses projects with compiled targets.
        args.append("--backend=none")
    return dispatch(scope, args)


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    if sys.argv[1:2] == ["--tool"]:
        from pcc.tools.meson_cc import run
        if len(sys.argv) < 3:
            raise ValueError("missing PCC Meson tool")
        # Target compiler invocations require only PCC and the standard library.
        return run(sys.argv[2], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meson-source", type=Path, required=True)
    parser.add_argument("--configuration-only", action="store_true",
                        help="select stock Meson's targetless 'none' backend; not for package build qualification")
    parser.add_argument("source", type=Path)
    parser.add_argument("build", type=Path)
    args = parser.parse_args()
    return prepare(args.source, args.build, args.meson_source, config_only=args.configuration_only)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"pcc-meson: {error}", file=sys.stderr)
        raise SystemExit(2)

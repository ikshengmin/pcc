"""Bind actual runner tools, without installing/faking missing prerequisites.

This attests a direct rustc installation, not a source-certification token. Every
Rust checked-source test must still run check_rust_source on its exact bytes,
options and target. Absent target libcore/libstd is explicitly UNRUN.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    return result.stdout



def version_observation(name, invocation, directory):
    arguments = {"go": ["version"], "java": ["-version"], "javac": ["-version"], "rustc": ["-vV"]}
    command = [invocation, *arguments.get(name, ["--version"])]
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
        stdout, stderr = result.stdout, result.stderr
        observation = {"command": command, "status": "COMPLETE", "exit_code": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        stdout, stderr = getattr(error, "stdout", None) or b"", getattr(error, "stderr", None) or b""
        observation = {"command": command, "status": type(error).__name__, "exit_code": None, "error": str(error)}
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "version.stdout.bin").write_bytes(stdout)
    (directory / "version.stderr.bin").write_bytes(stderr)
    observation.update(stdout=stdout.decode("utf-8", "replace"), stderr=stderr.decode("utf-8", "replace"),
                       stdout_base64=base64.b64encode(stdout).decode("ascii"),
                       stderr_base64=base64.b64encode(stderr).decode("ascii"),
                       stdout_sha256=hashlib.sha256(stdout).hexdigest(), stderr_sha256=hashlib.sha256(stderr).hexdigest())
    return observation


def verify_rust_installation(receipt):
    """CI owns closure verification; the production adapter only pins rustc.

    Use the receipt's existing installed_files schema, not the descriptive
    targets map. Never call a compiler or mint a source-certification token.
    """
    root = Path(receipt["new_prefix"]).resolve(strict=True)
    rows = receipt["installed_files"]
    expected = {}
    for row in rows:
        path = Path(row["path"])
        if path in expected or not path.is_absolute() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("invalid or duplicate audited Rust installation path")
        expected[path] = row["sha256"]
        if not path.is_file() or digest(path) != row["sha256"]:
            raise ValueError("audited Rust installation file changed: " + str(path))
    compiler = root / "bin/rustc"
    if compiler.is_symlink() or compiler not in expected:
        raise ValueError("direct Rust compiler missing from audited installation")
    libraries = {path for path in (root / "lib").rglob("*") if path.is_file()}
    if libraries | {compiler} != set(expected):
        raise ValueError("Rust compiler/target library inventory differs from audited installation")
    if not list((root / "lib").glob("librustc_driver*")):
        raise ValueError("actual Rust compiler driver library absent")
    return {"verified_files": len(expected), "claim": "installation bytes only; no source certification"}




def consumed_rust_receipt_identity(tool_receipt, path):
    """Bind the separate JSON that Rust tests actually open, not just its copy."""
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    consumed = json.loads(data)
    installation = tool_receipt.get("rust_installation")
    if installation is None or consumed != installation:
        raise ValueError("consumed Rust receipt differs from setup tool installation")
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def verify_tool_receipt(receipt):
    """Stream-hash every declared generic tool and complete Rust installation."""
    verified = []
    for name, row in receipt.items():
        if name == "rust_installation":
            if "installed_files" in row:
                verified.append({"tool": name, **verify_rust_installation(row)})
            continue
        if "invocation" not in row:
            if row.get("path_lookup") and shutil.which(name) is not None:
                raise ValueError("absent audited PATH tool appeared: " + name)
            continue  # Explicit absent prerequisite, never an admitted tool.
        # Some original SDK tests invoke clang directly through PATH, not
        # PCC_CPP_ORACLE. Bind the exact lookup as well as the executable bytes.
        if row.get("path_lookup") and shutil.which(name) != row["invocation"]:
            raise ValueError("audited tool PATH invocation changed: " + name)
        path = Path(row["invocation"])
        if str(path.resolve(strict=True)) != row["resolved"] or digest(path) != row["sha256"]:
            raise ValueError("audited tool identity changed: " + name)
        verified.append({"tool": name, "path": str(path), "sha256": row["sha256"]})
    return {"verified": verified, "claim": "declared tool bytes only; execution results are separate"}


def rust_installation(compiler):
    compiler = Path(compiler)
    if compiler.is_symlink() or not compiler.is_file():
        raise ValueError("rustc must be a direct executable, not a symlink/proxy")
    root = Path(execute([str(compiler), "--print=sysroot"]).strip()).resolve(strict=True)
    if compiler.absolute() != root / "bin/rustc":
        raise ValueError("rustc invocation is not the direct sysroot executable")
    version = execute([str(compiler), "-vV"])
    if not version.startswith("rustc "):
        raise ValueError("missing actual rustc verbose version")
    # Include all installed compiler/target libraries: driver, LLVM/codegen
    # helpers and every dependency of target core/std, in the consumed schema.
    libraries = sorted(path for path in (root / "lib").rglob("*") if path.is_file())
    files = [{"path": str(path), "sha256": digest(path)} for path in [compiler, *libraries]]
    file_rows = {row["path"]: row for row in files}
    targets = {}
    for target in ("x86_64-unknown-linux-gnu", "aarch64-apple-darwin"):
        lib = root / "lib/rustlib" / target / "lib"
        libraries = sorted(set(lib.glob("libcore-*.rlib")) | set(lib.glob("libstd-*.rlib")))
        ready = bool(list(lib.glob("libcore-*.rlib")) and list(lib.glob("libstd-*.rlib")))
        targets[target] = {"status": "AVAILABLE" if ready else "UNRUN_PREREQUISITE",
                           "reason": "actual matching target core/std" if ready else "matching target libcore/libstd absent",
                           "files": [file_rows[str(path)] for path in libraries]}
    receipt = {"new_prefix": str(root), "version": version, "installed_files": files,
               "targets": targets, "claim": "installation identity only; no Rust source was certified"}
    receipt["closure_verification"] = verify_rust_installation(receipt)
    return receipt



def selected_rustc_invocation():
    selected = os.environ.get("PCC_CI_RUST_TOOLCHAIN")
    if selected:
        if selected != "1.91.1" or not shutil.which("rustup"):
            raise ValueError("pinned official Rust toolchain unavailable")
        # Resolve the installed pinned executable before any rustc invocation.
        # Running the default rustup proxy can inspect/install another toolchain.
        return execute(["rustup", "which", "--toolchain", selected, "rustc"]).strip()
    return shutil.which("rustc")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    receipt, environment = {}, {}
    tools = {"cpp": ("g++", "clang"), "java": ("java", "javac"), "go": ("go",),
             "rust": ("rustc", "cc"), "shared": ("cc", "as", "ld", "clang", "rustc"),
             "ci-contracts": ()}[args.domain]
    for name in tools:
        java_home = os.environ.get("JAVA_HOME_21_X64")
        candidate = Path(java_home) / "bin" / name if java_home and name in {"java", "javac"} else None
        try:
            invocation = (selected_rustc_invocation() if name == "rustc" else
                          str(candidate) if candidate and candidate.is_file() else shutil.which(name))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            receipt[name] = {"status": "UNRUN_PREREQUISITE", "reason": str(error)}
            continue
        if not invocation:
            receipt[name] = {"status": "UNRUN_PREREQUISITE", "reason": "tool absent from runner PATH",
                             "path_lookup": name == "clang"}
            continue
        path = Path(invocation)
        observation = version_observation(name, str(path), args.out / name)
        available = observation["status"] == "COMPLETE" and observation["exit_code"] == 0
        receipt[name] = {"status": "AVAILABLE" if available else "UNRUN_PREREQUISITE",
                         "invocation": str(path), "resolved": str(path.resolve()),
                         "sha256": digest(path), "version_observation": observation,
                         "path_lookup": name == "clang"}
    if "g++" in receipt and receipt["g++"]["status"] == "AVAILABLE":
        environment["PCC_CPP_ORACLE"] = receipt["g++"]["invocation"]
    if "go" in receipt and receipt["go"]["status"] == "AVAILABLE":
        environment["PCC_GO_REFERENCE"] = receipt["go"]["invocation"]
        environment["PCC_GO_REFERENCE_CACHE"] = str(args.out / "go-cache")
        (args.out / "go-cache").mkdir()
    if all(receipt.get(name, {}).get("status") == "AVAILABLE" for name in ("java", "javac")):
        environment["PCC_JAVA_RAW_REFERENCE"] = str(Path(receipt["java"]["invocation"]).resolve())
        environment["PCC_JAVA_RAW_REFERENCE_SOURCE"] = "21"
    if "rustc" in receipt and receipt["rustc"]["status"] == "AVAILABLE":
        try:
            compiler = receipt["rustc"]["invocation"]
            # The workflow installs official pinned payloads. Here rustup only
            # locates the real executable; version labels never replace hashes
            # or actual core/std discovery and per-input source certification.
            selected = os.environ.get("PCC_CI_RUST_TOOLCHAIN")
            if not selected and shutil.which("rustup"):
                compiler = execute(["rustup", "which", "rustc"]).strip()
            installation = rust_installation(compiler)
            if selected and not installation["version"].startswith("rustc 1.91.1 "):
                raise ValueError("actual Rust compiler version differs from pinned installation")
            path = args.out / "rust-installation.json"
            path.write_text(json.dumps(installation, indent=2) + "\n")
            if installation["targets"]["x86_64-unknown-linux-gnu"]["status"] == "AVAILABLE":
                environment["PCC_RUSTC_INSTALL_RECEIPT"] = str(path)
                # Native reference helpers also invoke rustc by PATH; align it
                # with the exact direct executable whose bytes were attested.
                with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as output:
                    output.write(str(Path(compiler).parent) + "\n")
            receipt["rust_installation"] = installation
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            receipt["rust_installation"] = {"status": "UNRUN_PREREQUISITE", "reason": str(error)}
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    environment["PCC_CI_TOOL_RECEIPT"] = str(args.out / "receipt.json")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
        for key, value in sorted(environment.items()):
            if "\n" in value or "\r" in value:
                raise ValueError("invalid environment path")
            output.write(key + "=" + value + "\n")


if __name__ == "__main__":
    main()

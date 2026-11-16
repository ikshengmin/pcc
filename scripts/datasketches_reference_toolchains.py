"""Install the pinned reference toolchains for the DataSketches comparison.

    uv run python scripts/datasketches_reference_toolchains.py [--home DIR]

Development-only and outside the repository: DIR (default
$PCC_DATASKETCHES_TOOLCHAINS, else ~/.cache/pcc/reference-toolchains) receives
the official Go and Temurin JDK archives, verified against the SHA-256 the
publisher lists (go.dev/dl, api.adoptium.net), extracted with the tarfile data
filter, and a Python virtual environment with exact versions of CMake,
nanobind, numpy, setuptools and wheel for building datasketches-python. Go
module sources for datasketches-go are fetched by the Go tool itself into
DIR/gomodcache and verified against the pinned go.sum. DIR/receipt.json
records every path, version and digest; scripts/datasketches_bench.py reads
it. These are reference tools: they never build or substitute a PCC arm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

DEFAULT_HOME = Path(os.environ.get("PCC_DATASKETCHES_TOOLCHAINS", "~/.cache/pcc/reference-toolchains")).expanduser()

# Publisher-listed digests (go.dev/dl JSON, api.adoptium.net assets).
ARCHIVES = {
    ("darwin", "arm64"): {
        "go": {"version": "go1.27.3", "url": "https://go.dev/dl/go1.27.3.darwin-arm64.tar.gz",
               "size": 68196737,
               "sha256": "dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2"},
        "jdk": {"version": "jdk-25.0.5+8",
                "url": "https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/"
                       "OpenJDK25U-jdk_aarch64_mac_hotspot_25.0.5_8.tar.gz",
                "size": 136446056,
                "sha256": "36dc14d797aaff6d89aab302cd053fdba6207dfdcf772052fa7b023a94b0e857"},
    },
}
# nanobind 2.5.0 (2025-02-02) is contemporary with datasketches-python 5.2.0
# (2025-03-01, nanobind >= 1.6); later major versions changed its API.
PYTHON = "3.14"
PYTHON_REQUIREMENTS = ("cmake==4.4.5", "nanobind==2.5.0", "numpy==2.5.5", "setuptools==84.2.1", "wheel==0.48.0")


def receipt_path(home=DEFAULT_HOME):
    return Path(home) / "receipt.json"


def load_receipt(home=DEFAULT_HOME):
    path = receipt_path(home)
    return json.loads(path.read_text()) if path.is_file() else None


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(pin, downloads):
    target = downloads / pin["url"].rsplit("/", 1)[-1].replace("%2B", "+")
    if not target.is_file() or sha256(target) != pin["sha256"]:
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(pin["url"], timeout=600) as response, open(partial, "wb") as stream:
            shutil.copyfileobj(response, stream)
        if partial.stat().st_size != pin["size"] or sha256(partial) != pin["sha256"]:
            partial.unlink()
            raise RuntimeError("downloaded archive does not match its published digest: " + pin["url"])
        partial.replace(target)
    return target


def extract(archive, destination):
    if destination.exists():
        return destination
    staging = destination.with_name(destination.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
    staging.replace(destination)
    return destination


def install(home):
    key = (sys.platform, platform.machine())
    if key not in ARCHIVES:
        raise SystemExit(f"no pinned reference toolchains for {key}")
    home.mkdir(parents=True, exist_ok=True)
    downloads = home / "downloads"
    downloads.mkdir(exist_ok=True)
    pins = ARCHIVES[key]
    go_root = extract(fetch(pins["go"], downloads), home / pins["go"]["version"]) / "go"
    jdk_root = extract(fetch(pins["jdk"], downloads), home / pins["jdk"]["version"])
    java_home = next(path.parent.parent for path in jdk_root.glob("*/Contents/Home/bin/java"))
    venv = home / f"python{PYTHON}-datasketches"
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required to create the reference Python environment")
    if not (venv / "bin/python").exists():
        subprocess.run([uv, "venv", "--python", PYTHON, str(venv)], check=True)
    subprocess.run([uv, "pip", "install", "--python", str(venv / "bin/python"), *PYTHON_REQUIREMENTS], check=True)
    freeze = subprocess.run([uv, "pip", "freeze", "--python", str(venv / "bin/python")], check=True,
                            capture_output=True, text=True).stdout.split()
    versions = {
        "go": subprocess.run([str(go_root / "bin/go"), "version"], check=True, capture_output=True, text=True).stdout.strip(),
        "java": subprocess.run([str(java_home / "bin/java"), "-version"], check=True, capture_output=True,
                               text=True).stderr.splitlines()[0],
        "python": subprocess.run([str(venv / "bin/python"), "--version"], check=True, capture_output=True,
                                 text=True).stdout.strip(),
    }
    receipt = {"schema_version": 1, "platform": list(key), "archives": pins,
               "go_root": str(go_root), "gomodcache": str(home / "gomodcache"),
               "java_home": str(java_home), "python": str(venv / "bin/python"),
               "python_requirements": list(PYTHON_REQUIREMENTS), "python_freeze": freeze, "versions": versions}
    receipt_path(home).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    arguments = parser.parse_args(argv)
    receipt = install(arguments.home.expanduser())
    print(json.dumps(receipt["versions"], indent=2))
    print("receipt:", receipt_path(arguments.home.expanduser()))


if __name__ == "__main__":
    main()

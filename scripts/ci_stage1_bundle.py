"""Bounded, same-workflow Stage1 transport; no builds or native execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import struct
import sys
import tarfile
import tempfile

SCHEMA = "pcc.ci-stage1-bundle.v2"
CONFIG = {"threads": True, "refcount": "atomic"}
RUNTIME = "build/worker-prior-gate/runtime/libpy_runtime_pcc_py.a"
STAGE = "build/worker-prior-gate/stage1.json"
PROFILE = "build/worker-prior-gate/profile/stage1.result.json"
SHARED = "build/platform-qualification/shared/"
PLATFORMS = {
    "linux-x86_64": {"target": "x86_64-unknown-linux-gnu", "format": "elf", "machine": 62},
    "linux-aarch64": {"target": "aarch64-unknown-linux-gnu", "format": "elf", "machine": 183},
    "macos-arm64": {"target": "arm64-apple-darwin", "format": "macho", "machine": 0x100000c},
    "windows-x86_64": {"target": "x86_64-pc-windows-msvc", "format": "pe", "machine": 0x8664},
}
SAFE_MODES = {0o600, 0o644, 0o664, 0o700, 0o755, 0o775}
MAX_FILE = 2**31
MAX_TOTAL = 4 * 2**30
MAX_MANIFEST = 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_digest(value, length=64):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def host_platform():
    machine = platform.machine().lower()
    if sys.platform.startswith("linux"):
        name = "linux-x86_64" if machine in ("x86_64", "amd64") else "linux-aarch64" if machine in ("aarch64", "arm64") else None
    elif sys.platform == "darwin":
        name = "macos-arm64" if machine in ("aarch64", "arm64") else None
    elif sys.platform == "win32":
        name = "windows-x86_64" if machine in ("x86_64", "amd64") else None
    else:
        name = None
    require(name is not None, "unsupported native Stage1 host")
    return name


def workflow_lease(environ=None):
    """Bind to this run and attempt, not an independently selected artifact run."""
    env = os.environ if environ is None else environ
    repository = env.get("GITHUB_REPOSITORY", "")
    reference = env.get("GITHUB_WORKFLOW_REF", "")
    prefix = repository + "/"
    require(repository.count("/") == 1 and all(repository.split("/")), "missing workflow repository")
    require(reference.startswith(prefix) and "@" in reference, "missing workflow reference")
    workflow, ref = reference[len(prefix):].split("@", 1)
    require(workflow == ".github/workflows/pcc1-package-parity.yml" and bool(ref), "invalid workflow reference")
    values = {}
    for key in ("run_id", "run_attempt"):
        raw = env.get("GITHUB_" + key.upper(), "")
        require(raw.isascii() and raw.isdecimal() and int(raw) > 0, "missing workflow " + key)
        values[key] = int(raw)
    sha = env.get("GITHUB_SHA", "")
    require(valid_digest(sha, 40), "invalid workflow source SHA")
    return {"repository": repository, "workflow": workflow, **values, "sha": sha}


def layout(platform_id):
    require(platform_id in PLATFORMS, "unsupported bundle platform")
    mac = platform_id == "macos-arm64"
    suffix = ".exe" if platform_id == "windows-x86_64" else ""
    paths = {"compiler": "build/bootstrap/pcc1" if mac else SHARED + "pcc1" + suffix,
             "runtime": RUNTIME, "stage_receipt": STAGE,
             "shared_receipt": None if mac else SHARED + "receipt.json"}
    files = {paths["compiler"], RUNTIME, RUNTIME + ".provenance.json", RUNTIME + ".capi_syms", STAGE}
    if mac:
        files.add(PROFILE)
    else:
        files.add(paths["shared_receipt"])
        files.update(SHARED + name for name in (
            "canary.py", "canary1" + suffix, "canary1-run.log", "stage1.log", "stage1.resources.jsonl",
            "canary1-compile.log", "canary1-compile.resources.jsonl", "canary1-run.resources.jsonl"))
    return paths, files


def regular_file(path, root):
    path, root = Path(path), Path(root)
    require(path.is_relative_to(root), "file escapes checkout")
    for parent in (path, *path.parents):
        if parent == root:
            break
        require(not parent.is_symlink(), "export input must be a regular, non-symlink file: " + str(path))
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "export input must be a regular, non-symlink file: " + str(path))
    return info


def verify_native_header(path, platform_id):
    """Read exact native ABI headers; a magic prefix alone is insufficient."""
    spec = PLATFORMS[platform_id]
    with Path(path).open("rb") as stream:
        data = stream.read(64)
        if spec["format"] == "elf":
            require(len(data) == 64 and data[:7] == b"\x7fELF\x02\x01\x01", "compiler ELF64 ABI differs")
            kind, machine, version = struct.unpack_from("<HHI", data, 16)
            require(kind in (2, 3) and machine == spec["machine"] and version == 1
                    and struct.unpack_from("<H", data, 52)[0] == 64, "compiler ELF64 machine/type differs")
        elif spec["format"] == "macho":
            require(len(data) >= 32 and data[:4] == b"\xcf\xfa\xed\xfe", "compiler Mach-O64 ABI differs")
            cpu, subtype, kind = struct.unpack_from("<III", data, 4)
            require(cpu == spec["machine"] and kind == 2, "compiler Mach-O64 machine/type differs")
        else:
            require(len(data) == 64 and data[:2] == b"MZ", "compiler PE32+ DOS header differs")
            offset = struct.unpack_from("<I", data, 60)[0]
            require(64 <= offset <= 1024 * 1024, "compiler PE header offset differs")
            stream.seek(offset)
            pe = stream.read(26)
            require(len(pe) == 26 and pe[:4] == b"PE\0\0", "compiler PE signature differs")
            machine, sections = struct.unpack_from("<HH", pe, 4)
            optional, flags, magic = struct.unpack_from("<HHH", pe, 20)
            require(machine == spec["machine"] and sections > 0 and optional >= 112 and magic == 0x20b
                    and flags & 2 and not flags & 0x2000, "compiler PE32+ AMD64 ABI differs")
            stream.seek(0, 2)
            require(stream.tell() >= offset + 24 + optional + sections * 40, "truncated compiler PE headers")


def validate_receipts(directory, manifest):
    """Cross-bind untouched producer receipts to the transported payloads."""
    rows, source, paths = manifest["files"], manifest["source"], manifest["paths"]
    stage = json.loads((directory / STAGE).read_text())
    require(stage.get("phase") == "stage1" and stage.get("status") == "PASS", "export requires a PASS Stage1 receipt")
    require(all(stage["source"].get(k) == source[k] for k in ("commit", "source_sha256", "clean_commit")), "source differs from the admitted clean checkout")
    runtime = stage["runtime"]
    require(runtime.get("target") == manifest["target"] and runtime.get("config") == CONFIG, "runtime identity differs from admitted configuration")
    require(stage["compiler"]["sha256"] == rows[paths["compiler"]]["sha256"], "compiler receipt hash differs")
    # Bind original absolute producer paths to the transported manifest.
    # import_bundle additionally requires the same checkout path, as before.
    require(stage["compiler"]["path"] == rows[paths["compiler"]]["source_path"], "compiler path differs from producer")
    require(runtime["path"] == rows[RUNTIME]["source_path"], "runtime path differs from producer")
    for suffix in ("", ".provenance.json", ".capi_syms"):
        require(runtime["hashes"][suffix] == rows[RUNTIME + suffix]["sha256"], "runtime receipt hash differs")
    if paths["shared_receipt"] is None:
        result = json.loads((directory / PROFILE).read_text())
        require(result.get("schema") == "pcc.bootstrap_stage_result.v1" and result.get("stage") == 1
                and result.get("backend") == "self" and result.get("returncode") == 0
                and result.get("publish_barrier_returncode") == 0
                and result.get("output") == stage["compiler"]["path"], "Darwin Stage1 publish receipt differs")
    else:
        shared = json.loads((directory / paths["shared_receipt"]).read_text())
        require(all(shared["source"].get(k) == source[k] for k in ("commit", "source_sha256", "clean_commit")), "source differs from the admitted clean checkout")
        require(shared.get("target") == manifest["target"] and shared["compiler_path"] == stage["compiler"]["path"], "compiler path or target differs from Stage1 producer")
        stages = shared["stages"]
        require(len(stages) == 1 and stages[0]["stage"] == 1 and stages[0]["sha256"] == stage["compiler"]["sha256"], "compiler hash differs between original Stage1 receipts")
        require(shared["runtime"]["path"] == runtime["path"], "shared receipt runtime path differs")
        for suffix, key in (("", "sha256"), (".provenance.json", "provenance_sha256"), (".capi_syms", "capi_inventory_sha256")):
            require(shared["runtime"][key] == runtime["hashes"][suffix], "runtime receipt hash differs: " + suffix)
        canary = stages[0]["canary"]
        suffix = ".exe" if manifest["platform"] == "windows-x86_64" else ""
        for name, key in (("canary.py", "source_sha256"), ("canary1" + suffix, "executable_sha256"), ("canary1-run.log", "output_sha256")):
            require(canary[key] == rows[SHARED + name]["sha256"], "canary receipt hash differs")


class _RegularTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Reject extensions before tarfile reads their potentially huge payload.
        require(self.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not self.issparse(), "nonregular tar member")
        require(0 <= self.size <= MAX_FILE, "tar member size exceeds admission")
        return super()._proc_member(archive)


def import_bundle(root, bundle, commit, platform_id, lease):
    """Stage a bounded allowlist, validate fully, then publish without overwrite."""
    root, bundle = Path(root).resolve(strict=True), Path(bundle).resolve(strict=True)
    paths, allowed = layout(platform_id)
    mf = bundle / "manifest.json"
    require(regular_file(mf, bundle).st_size <= MAX_MANIFEST, "bundle manifest exceeds admission")
    manifest_bytes = mf.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest.get("schema") == SCHEMA, "bundle schema differs")
    require(manifest.get("platform") == platform_id and manifest.get("target") == PLATFORMS[platform_id]["target"]
            and manifest.get("runtime_config") == CONFIG and manifest.get("paths") == paths, "bundle platform/configuration differs")
    require(manifest.get("producer") == lease and lease.get("sha") == commit, "bundle workflow lease differs")
    for key in ("compiler", "runtime"):
        require(manifest["files"][paths[key]].get("source_path") == str(root / paths[key]),
                "producer checkout path differs; relocation is not admitted")
    source = manifest.get("source", {})
    require(valid_digest(commit, 40) and source.get("commit") == commit and source.get("clean_commit") is True
            and valid_digest(source.get("source_sha256")), "bundle source differs")
    rows = manifest["files"]
    require(set(rows) == allowed, "bundle file inventory differs")
    for name, row in rows.items():
        require(type(row.get("size")) is int and 0 <= row["size"] <= MAX_FILE
                and valid_digest(row.get("sha256")) and type(row.get("mode")) is int
                and row["mode"] in SAFE_MODES | ({0o666, 0o777} if platform_id == "windows-x86_64" else set())
                and isinstance(row.get("source_path"), str), "unsafe artifact metadata")
    require(sum(row["size"] for row in rows.values()) <= MAX_TOTAL, "bundle size exceeds admission")
    require(regular_file(bundle / "stage1-native.tar.gz", bundle).st_size <= MAX_TOTAL, "compressed bundle exceeds admission")
    build = root / "build"
    require(not build.is_symlink(), "destination escapes checkout")
    build.mkdir(exist_ok=True)

    def destination(name):
        path = root / name
        require(not path.exists() and not path.is_symlink(), "refuse existing destination: " + name)
        for parent in path.parents:
            if parent == root:
                break
            require(not parent.is_symlink(), "destination escapes checkout")
        require(path.resolve().is_relative_to(root), "destination escapes checkout")
        return path

    for name in allowed:
        destination(name)
    with tempfile.TemporaryDirectory(prefix="pcc-marker-import-", dir=build) as temporary:
        staging = Path(temporary)
        seen = set()
        with tarfile.open(bundle / "stage1-native.tar.gz", "r|gz", tarinfo=_RegularTarInfo) as archive:
            for member in archive:
                name = member.name
                require(name not in seen and name in allowed | {"manifest.json"}, "duplicate/unexpected tar member")
                seen.add(name)
                if name == "manifest.json":
                    require(member.size == len(manifest_bytes), "embedded manifest size differs")
                    require(archive.extractfile(member).read() == manifest_bytes, "embedded manifest differs")
                    continue
                row = rows[name]
                require(member.size == row["size"] and member.mode == row["mode"], "tar metadata differs")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                require(target.stat().st_size == row["size"] and digest(target) == row["sha256"], "artifact hash differs: " + name)
                target.chmod(row["mode"])
        require(seen == allowed | {"manifest.json"}, "missing tar member")
        validate_receipts(staging, manifest)
        verify_native_header(staging / paths["compiler"], platform_id)
        require(platform_id == "windows-x86_64" or rows[paths["compiler"]]["mode"] & 0o111, "compiler is not executable")
        if paths["shared_receipt"]:
            verify_native_header(staging / (SHARED + "canary1" + (".exe" if platform_id == "windows-x86_64" else "")), platform_id)
        published = []
        try:
            for name in sorted(allowed):
                path = destination(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output, (staging / name).open("rb") as stream:
                    published.append(path)
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                path.chmod(rows[name]["mode"])
        except BaseException:
            for path in published:
                path.unlink()
            raise
    return manifest


def admit_in_place(root, bundle, commit, platform_id, lease):
    """Validate an existing producer export without extraction or rebuilding."""
    root, bundle = Path(root).resolve(strict=True), Path(bundle).resolve(strict=True)
    mf = bundle / "manifest.json"
    require(regular_file(mf, bundle).st_size <= MAX_MANIFEST, "bundle manifest exceeds admission")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    paths, allowed = layout(platform_id)
    require(manifest.get("schema") == SCHEMA and manifest.get("platform") == platform_id
            and manifest.get("target") == PLATFORMS[platform_id]["target"]
            and manifest.get("runtime_config") == CONFIG and manifest.get("paths") == paths,
            "bundle platform/configuration differs")
    require(manifest.get("producer") == lease and lease.get("sha") == commit,
            "bundle workflow lease differs")
    source = manifest.get("source", {})
    require(valid_digest(commit, 40) and source.get("commit") == commit
            and source.get("clean_commit") is True and valid_digest(source.get("source_sha256")),
            "bundle source differs")
    rows = manifest["files"]
    require(set(rows) == allowed, "bundle file inventory differs")
    for name, row in rows.items():
        path = root / name
        info = regular_file(path, root)
        require(row.get("source_path") == str(path) and type(row.get("size")) is int
                and 0 <= row["size"] <= MAX_FILE and info.st_size == row["size"]
                and valid_digest(row.get("sha256")) and digest(path) == row["sha256"]
                and type(row.get("mode")) is int
                and row["mode"] in SAFE_MODES | ({0o666, 0o777} if platform_id == "windows-x86_64" else set())
                and stat.S_IMODE(info.st_mode) == row["mode"], "in-place artifact bytes/metadata differ: " + name)
    require(sum(row["size"] for row in rows.values()) <= MAX_TOTAL, "bundle size exceeds admission")
    validate_receipts(root, manifest)
    verify_native_header(root / paths["compiler"], platform_id)
    require(platform_id == "windows-x86_64" or rows[paths["compiler"]]["mode"] & 0o111,
            "compiler is not executable")
    if paths["shared_receipt"]:
        verify_native_header(root / (SHARED + "canary1" + (".exe" if platform_id == "windows-x86_64" else "")), platform_id)
    return manifest

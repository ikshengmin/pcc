"""Guarded, input-only preparation for pcc_emit_rank and future A/B callers.

No CLI, workflow admission, performance verdict or optimization activation.
The caller must own the real enclosing sampler and performance lock. Hashes
bind integrity, not arbitrary JSON to execution authority. A fresh native
coordinator produces the plan; imported plans/environments are not accepted.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORT = REPO_ROOT / "scripts/run_pcc_compile_ab.py"
SCHEMA = "pcc.indexed-input-capture.v1"
PLAN_SCHEMA = "pcc.frontend-codegen-plan.v2"
WORKER_SCHEMA = "pcc.frontends.python.codegen_worker.v4"
_LIVE_CAPTURES = {}


class IndexedCaptureError(RuntimeError):
    pass


def _require(condition, message):
    if not condition:
        raise IndexedCaptureError(message)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)


def _identity(info):
    return info.st_dev, info.st_ino


def _leaf(name):
    _require(isinstance(name, str) and name not in ("", ".", "..")
             and Path(name).name == name and "/" not in name and "\\" not in name,
             "capture output name must be a single path component")
    return name


def _temporary_name():
    return ".pcc-capture-" + secrets.token_hex(16) + ".tmp"


def _close_descriptor(descriptor):
    try:
        os.close(descriptor)
    except BaseException:
        pass


class _OutputOwner:
    """Pin exclusively created directories; never follow cleanup/publication paths.

    A namespace mismatch revokes disk authority, including over the still-open
    original directory. Relative dirfds also prevent a concurrent replacement
    from redirecting an operation into the replacement tree.
    """

    def __init__(self, path, descriptor, parent_descriptor):
        self.path = path
        self.closed = False
        self.parent_descriptor = parent_descriptor
        self.parent_identity = _identity(os.fstat(parent_descriptor))
        self.ancestors = {parent: _identity(_path(str(parent), directory=True).stat())
                          for parent in path.parents}
        self.directories = {"": (descriptor, _identity(os.fstat(descriptor)), None, None)}
        self.files = {}

    def verify(self):
        _require(not self.closed, "capture output owner is closed")
        _require(_identity(os.fstat(self.parent_descriptor)) == self.parent_identity,
                 "capture output parent descriptor changed")
        for parent, identity in self.ancestors.items():
            _require(_identity(_path(str(parent), directory=True).stat()) == identity,
                     "capture output ancestor was replaced")
        root_entry = os.stat(self.path.name, dir_fd=self.parent_descriptor, follow_symlinks=False)
        _require(stat.S_ISDIR(root_entry.st_mode)
                 and _identity(root_entry) == self.directories[""][1],
                 "capture output entry was replaced")
        for key, (descriptor, identity, parent, name) in self.directories.items():
            opened = os.fstat(descriptor)
            _require(stat.S_ISDIR(opened.st_mode) and _identity(opened) == identity,
                     "capture owned directory descriptor changed")
            current = _path(str(self.path / key), directory=True).stat()
            _require(_identity(current) == identity, "capture owned directory was replaced")
            if parent is not None:
                relative = os.stat(name, dir_fd=self.directories[parent][0], follow_symlinks=False)
                _require(stat.S_ISDIR(relative.st_mode) and _identity(relative) == identity,
                         "capture owned child directory was replaced")
        for (directory, name), identity in self.files.items():
            current = os.stat(name, dir_fd=self.directories[directory][0], follow_symlinks=False)
            _require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                     and _identity(current) == identity, "capture owned file was replaced")

    def close(self):
        if self.closed:
            return
        self.closed = True
        for descriptor, _, _, _ in reversed(list(self.directories.values())):
            _close_descriptor(descriptor)
        _close_descriptor(self.parent_descriptor)

    def mkdir(self, name, *, directory=""):
        _leaf(name)
        self.verify()
        parent = self.directories[directory][0]
        os.mkdir(name, mode=0o700, dir_fd=parent)
        created = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        key = directory + "/" + name if directory else name
        try:
            opened = os.fstat(descriptor)
            _require(stat.S_ISDIR(created.st_mode) and _identity(opened) == _identity(created),
                     "capture created directory changed before acquisition")
            self.directories[key] = (descriptor, _identity(opened), directory, name)
        except BaseException:
            _close_descriptor(descriptor)
            raise
        self.verify()
        return self.path / key

    def create(self, name, *, directory="", text=None, source=None, mode=0o444):
        _leaf(name)
        self.verify()
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directories[directory][0])
        try:
            self.files[(directory, name)] = _identity(os.fstat(descriptor))
            stream = os.fdopen(descriptor, "wb")
        except BaseException:
            _close_descriptor(descriptor)
            raise
        with stream:
            if source is None:
                stream.write(text.encode("utf-8"))
            else:
                with source.open("rb") as original:
                    shutil.copyfileobj(original, stream)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        self.verify()
        return self.path / directory / name

    def publish(self, name, value, *, directory=""):
        _leaf(name)
        data = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        self.verify()
        parent = self.directories[directory][0]
        key = directory, name
        replacing = key in self.files
        if not replacing:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise IndexedCaptureError("capture publication target is not owned")
        descriptor = None
        for _ in range(8):
            temporary = _leaf(_temporary_name())
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                break
            except FileExistsError:
                continue
        _require(descriptor is not None, "capture exclusive temporary file is unavailable")
        try:
            identity = _identity(os.fstat(descriptor))
            stream = os.fdopen(descriptor, "w", encoding="utf-8")
        except BaseException:
            _close_descriptor(descriptor)
            raise
        try:
            with stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
            self.verify()
            current = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            _require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                     and _identity(current) == identity, "capture temporary file was replaced")
            if replacing:
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                # Linking an exclusively created inode is an atomic no-replace
                # publication; a newly planted name cannot be overwritten.
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.unlink(temporary, dir_fd=parent)
            self.files[key] = identity
            self.verify()
        except BaseException:
            # Do not even clean our scratch inode after namespace ownership is
            # lost. The live capability is revoked by the caller in all cases.
            try:
                self.verify()
                current = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                if (stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                        and _identity(current) == identity):
                    os.unlink(temporary, dir_fd=parent)
            except BaseException:
                pass
            raise

    def unlink(self, name, *, directory=""):
        self.verify()
        key = directory, _leaf(name)
        _require(key in self.files, "capture cleanup target is not owned")
        os.unlink(name, dir_fd=self.directories[directory][0])
        del self.files[key]
        self.verify()


def _persist(owner, name, value, *, directory=""):
    owner.publish(name, value, directory=directory)


def _close_output(context):
    try:
        if hasattr(context, "output_owner"):
            context.output_owner.close()
    except BaseException:
        pass


def _invalidate(context, exc):
    """Remove the reusable manifest and best-effort mark failure, never mask it."""
    owner = getattr(context, "output_owner", None)
    if owner is None:
        return
    try:
        owner.verify()
    except BaseException:
        return
    if ("corpus", "manifest.json") in owner.files:
        try:
            owner.unlink("manifest.json", directory="corpus")
        except BaseException:
            return
    try:
        message = str(exc)
    except BaseException:
        message = "exception text unavailable"
    failure = {"schema": SCHEMA, "status": "FAILED", "exception_type": type(exc).__name__,
               "message": message, "source_promotion_authorized": False}
    for name in ("capture.json", "capture.failure.json"):
        try:
            _persist(owner, name, failure)
        except BaseException:
            pass


def _path(raw, *, root=None, directory=False, read_only=False):
    path = Path(raw)
    _require(path.is_absolute() and str(path) == os.path.normpath(str(path)),
             "capture path must be canonical and absolute")
    _require(path.resolve(strict=True) == path, "capture path contains a symlink/alias")
    if root is not None:
        _require(path != root and root in path.parents, "capture path escapes its owner")
    mode = path.stat()
    _require(stat.S_ISDIR(mode.st_mode) if directory else stat.S_ISREG(mode.st_mode),
             "capture path has the wrong file kind")
    if not directory:
        _require(mode.st_nlink == 1, "capture file has a hard-link alias")
    if read_only:
        _require(not mode.st_mode & 0o222, "capture input must be read-only")
    return path


def _tree(root, ab, *, seal=False):
    _path(str(root), directory=True)
    files, directories = ab._snapshot_tree_entries(root, "capture dependency tree")
    for path in files:
        _path(str(path), root=root)
    if seal:
        for path in files:
            path.chmod(0o444)
        for path in sorted(directories, key=lambda value: len(value.parts), reverse=True):
            path.chmod(0o555)
    return {path.relative_to(root).as_posix(): ab._path_receipt(path)
            for path in sorted(files)}


def _current_source_files(ab):
    """Admit the complete known build closure, not a guessed protocol subset."""
    return {path.relative_to(REPO_ROOT).as_posix(): ab.sha256_path(path)
            for path in ab.build_source_files(REPO_ROOT)}


def _integer(text):
    _require(text.isascii() and text.isdecimal() and str(int(text)) == text,
             "noncanonical capture ordinal/count")
    return int(text)


def _new_output(raw, ab, protected):
    output = ab._validate_output_dir(raw)
    _require(not output.exists() and not output.is_symlink(), "capture output already exists")
    _require(output.resolve(strict=False) == output, "capture output has a symlink/alias parent")
    _require(not any(ab._paths_overlap(output, path) for path in protected),
             "capture output overlaps retained inputs/tools")
    parent = _path(str(output.parent), directory=True)
    before = parent.stat()
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    owner = None
    owned = None
    try:
        opened = os.fstat(descriptor)
        _require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 "capture output parent changed")
        os.mkdir(output.name, mode=0o700, dir_fd=descriptor)
        created = os.stat(output.name, dir_fd=descriptor, follow_symlinks=False)
        owned = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        _require(stat.S_ISDIR(created.st_mode) and _identity(os.fstat(owned)) == _identity(created),
                 "capture output changed before acquisition")
        owner = _OutputOwner(output, owned, descriptor)
        owned = None
        descriptor = None
        after = _path(str(parent), directory=True).stat()
        _require((after.st_dev, after.st_ino) == (opened.st_dev, opened.st_ino),
                 "capture output parent changed during creation")
        owner.verify()
    except BaseException:
        if owner is not None:
            owner.close()
        elif owned is not None:
            _close_descriptor(owned)
        raise
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor)
    return owner


def _worker(path, *, source, source_files, artifact_root, output):
    _path(str(path), root=output)
    rows = path.read_text(encoding="utf-8").splitlines()
    _require(len(rows) >= 14 and rows[0] == WORKER_SCHEMA, "capture requires a v4 manifest")
    _require(rows[4] == "codegen" and rows[7] == "on" and rows[8] in ("0", "1"),
             "capture requires ordinary scaffold-on codegen")
    _require(rows[2] == str(artifact_root), "worker artifact root differs from plan")
    # Result paths are NOT opened/deleted: only the newly rewritten worker runs.
    result = Path(rows[1])
    _require(result.is_absolute() and output in result.parents, "worker result escapes capture")
    exports = _path(rows[3], root=output)
    ast = _path(rows[5], root=output, directory=True)
    position = 10 + _integer(rows[9])
    _require(position < len(rows), "truncated sibling list")
    siblings = rows[10:position]
    _require(len(set(siblings)) == len(siblings), "duplicate sibling module")
    count = _integer(rows[position])
    position += 1
    _require(0 < count <= len(source_files) and position + count + 2 == len(rows),
             "capture requires one complete singleton assignment")
    modules = []
    for index in range(count):
        parts = rows[position + index].split("\t")
        _require(len(parts) == 3 and parts[0] == str(index) and parts[1],
                 "worker module ordinal/name is invalid")
        file = _path(parts[2], root=source, read_only=True)
        _require(file.relative_to(source).as_posix() in source_files,
                 "worker source is outside the compiler build closure")
        modules.append((parts[1], str(file)))
    _require(len(set(name for name, _ in modules)) == count and rows[-2] == "1",
             "duplicate module name or nonsingleton worker")
    index = _integer(rows[-1])
    _require(index < count and rows[6] in {name for name, _ in modules},
             "worker assignment/entry is outside its closure")
    return {"rows": rows, "index": index, "modules": modules,
            "exports": exports, "ast": ast,
            "context": (rows[3:10], siblings, modules)}


def _plan(path, *, compiler, runtime, source, source_files, output, module_name):
    _path(str(path), root=output)
    lines = path.read_text(encoding="utf-8").splitlines()
    _require(len(lines) >= 13 and lines[0] == PLAN_SCHEMA and lines[11] == "pidx-pco-v1",
             "coordinator did not produce a v2 indexed plan")
    count, oversized, jobs, manifest_count = [_integer(value) for value in lines[7:11]]
    _require(count > 0 and count == manifest_count and len(lines) == 12 + count
             and oversized <= count and 1 <= jobs <= 2, "invalid deferred plan counts")
    _require(lines[1] == str(compiler) and lines[3] == str(runtime),
             "deferred plan compiler/runtime differs from launch")
    for value in lines[2:3] + lines[4:6]:
        declared = Path(value)
        _require(declared.is_absolute() and output in declared.parents,
                 "deferred output escapes capture")
    artifact_root = _path(lines[6], root=output, directory=True)
    workers = []
    for raw in lines[12:]:
        workers.append(_worker(Path(raw), source=source, source_files=source_files,
                               artifact_root=artifact_root, output=output))
    context = workers[0]["context"]
    _require(len(context[2]) == count and all(row["context"] == context for row in workers)
             and {row["index"] for row in workers} == set(range(count)),
             "deferred workers do not share one complete closure")
    selected = [row for row in workers if row["modules"][row["index"]][0] == module_name]
    _require(len(selected) == 1, "requested module is missing or ambiguous")
    chosen = selected[0]
    ast_names = {"module_" + str(index) + ".json" for index in range(count)}
    actual = {path.name for path in chosen["ast"].iterdir() if path.is_file()}
    _require(ast_names <= actual, "deferred AST closure is incomplete")
    return chosen, [Path(raw) for raw in lines[12:]]


def _pidx_header(path, expected_target):
    """Framing/target observation only; real codec semantic validation is pending."""
    _path(str(path))
    with path.open("rb") as stream:
        _require(stream.readline(32) == b"PCCIDXMOD1\n", "missing indexed PIDX magic")
        size_line = stream.readline(32)
        _require(size_line.endswith(b"\n"), "invalid PIDX header length")
        size = _integer(size_line[:-1].decode("ascii"))
        # Same ceiling as the production codec, never a relaxed wire limit.
        _require(0 < size <= 512 * 1024 * 1024 and size <= path.stat().st_size,
                 "invalid PIDX header size")
        raw = stream.read(size)
        _require(len(raw) == size, "truncated PIDX header")
        header = json.loads(raw, object_pairs_hook=_unique)
        _require(type(header) is dict and header.get("schema") in (
            "pcc.self-backend.indexed-module.v1", "pcc.self-backend.indexed-module.v3")
            and header.get("scalar_width") == 8 and header.get("byte_order") == "little"
            and header.get("triple") == expected_target and "source_debug" not in header,
            "PIDX target/debug/framing contract mismatch")
        total = header.get("total_scalars")
        _require(type(total) is int and total >= 0 and stream.tell() + total * 8 == path.stat().st_size,
                 "PIDX payload length mismatch")
        return {"schema": header["schema"], "triple": expected_target,
                "source_debug": False, "total_scalars": total,
                "semantic_validation": "PENDING_REAL_CODEC_AND_EXECUTION"}


class _Context:
    def __init__(self, ab, token, budget):
        self.ab, self.token, self.budget = ab, token, budget
        self.functions = {name: getattr(ab, name) for name in (
            "_validated_worker_budget", "_run_process", "_load_build_receipt",
            "_verify_source_snapshot", "_verify_runtime_bundle", "_measurement_env")}
        self.tools = [ab._path_receipt(REPO_ROOT / name) for name in (
            "scripts/pcc_emit_rank.py", "scripts/pcc_indexed_capture.py",
            "scripts/run_pcc_compile_ab.py", "scripts/run_process_tree_sample.py",
            "scripts/file_lock.py")]
        self.files, self.trees = [], []

    def verify(self):
        _require(self.ab._HELD_PERFORMANCE_LOCK is self.token
                 and self.ab._ACTIVE_WORKER_BUDGET is self.budget
                 and all(getattr(self.ab, name) is function for name, function in self.functions.items()),
                 "capture live guard/lock/module changed")
        self.budget.verify()
        if hasattr(self, "output_owner"):
            self.output_owner.verify()
        for receipt in self.tools:
            self.ab._verify_receipt(receipt, "capture tool")

    def verify_inputs(self):
        self.verify()
        for receipt in self.files:
            _path(receipt["path"])
            self.ab._verify_receipt(receipt, "capture input")
            self.budget.verify()
        for root, expected in self.trees:
            _require(_tree(root, self.ab) == expected, "capture dependency tree changed")
            for raw in expected.values():
                _path(raw["path"], read_only=True)
            self.budget.verify()
        self.ab._verify_source_snapshot(self.source, self.source_manifest,
                                        "capture compiler source", require_read_only=True)
        _require(_current_source_files(self.ab) == self.source_manifest["files"],
                 "capture producer differs from current complete source closure")
        self.ab._verify_runtime_bundle(self.runtime_bundle)
        self.verify()

    def run(self, command, environment, cwd, prefix, timeout):
        self.verify_inputs()
        result = self.ab._run_process(command, env=environment, cwd=cwd,
                                      timeout=self.budget.remaining_timeout(timeout))
        # _run_process rejects surviving owned children before returning.
        if result.returncode != 0:
            try:
                self.verify_inputs()
                self.output_owner.create(prefix.with_suffix(".stdout").name, text=result.stdout)
                self.output_owner.create(prefix.with_suffix(".stderr").name, text=result.stderr)
            except BaseException:
                pass
            raise IndexedCaptureError("capture child failed: " + prefix.name
                                      + " rc=" + str(result.returncode))
        self.verify_inputs()
        self.output_owner.create(prefix.with_suffix(".stdout").name, text=result.stdout)
        self.output_owner.create(prefix.with_suffix(".stderr").name, text=result.stderr)
        self.verify_inputs()
        return {"command": command, "cwd": str(cwd), "environment": environment,
                "stdout": self.ab._path_receipt(prefix.with_suffix(".stdout")),
                "stderr": self.ab._path_receipt(prefix.with_suffix(".stderr")),
                "returncode": result.returncode}


def _copy_owned(context, source, name, *, directory="", mode=0o444):
    context.verify_inputs()
    before = context.ab._path_receipt(source)
    target = context.output_owner.create(name, directory=directory, source=source, mode=mode)
    after = context.ab._path_receipt(source)
    copied = context.ab._path_receipt(target)
    _require(before == after and copied["sha256"] == before["sha256"],
             "capture input changed while being snapshotted")
    context.verify_inputs()
    return target


def _capture(args, context):
    ab = context.ab
    ab.require_claim_platform()
    _require(args.compiler_arm in ("baseline", "candidate") and type(args.gc_backend) is int
             and args.gc_backend in range(5), "invalid compiler arm/collector")
    _require(args.python_ir_passes == "off", "first capture protocol requires explicit passes=off")
    _require(type(args.timeout) in (int, float) and math.isfinite(args.timeout) and args.timeout > 0,
             "invalid capture timeout")
    _require(isinstance(args.module_name, str) and args.module_name
             and all(part.isidentifier() for part in args.module_name.split(".")), "invalid module name")
    _require(isinstance(args.expected_target, str)
             and args.expected_target.startswith(("aarch64-apple-", "arm64-apple-")),
             "capture requires an explicit Darwin ARM target")
    compiler = _path(args.compiler)
    receipt = _path(args.build_receipt)
    runtime = _path(args.runtime_archive, read_only=True)
    _require(os.access(compiler, os.X_OK), "native compiler is not executable")
    with compiler.open("rb") as stream:
        _require(stream.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"),
                 "capture requires native Mach-O compiler bytes")
    loaded = ab._load_build_receipt(receipt, arm=args.compiler_arm,
        compiler_sha256=ab.sha256_path(compiler), compiler_size_bytes=compiler.stat().st_size,
        runtime_sha256=ab.sha256_path(runtime), expected_gc_backend=args.gc_backend)
    source = _path(loaded["source_snapshot_root"], directory=True, read_only=True)
    _require(_current_source_files(ab) == loaded["source_manifest"]["files"],
             "capture producer differs from current complete source closure")
    build_manifest = _path(str(receipt.parent / "manifest.json"))
    build = _json(build_manifest)
    _require(build.get("status") == "SUCCEEDED" and type(build.get("runtime_bundle")) is dict,
             "missing complete Stage1 runtime bundle")
    bundle = build["runtime_bundle"]
    _require(bundle.get("archive") == str(runtime)
             and ab.runtime_bundle_evidence(bundle) == loaded["receipt"]["runtime_bundle"],
             "runtime bundle differs from compiler build")
    runtime_root = runtime.parent
    for name, row in bundle["files"].items():
        _require(str(runtime_root / name) == row["path"], "runtime evidence path mismatch")
        _path(row["path"], root=runtime_root, read_only=True)
    context.source, context.source_manifest, context.runtime_bundle = source, loaded["source_manifest"], bundle
    context.files.extend(ab._path_receipt(path) for path in (compiler, receipt, build_manifest))
    evidence_paths = [Path(loaded["source_manifest_path"]), Path(loaded["stage_result_path"])]
    evidence_paths.extend(Path(path) for path in loaded["artifact_paths"].values())
    evidence_paths.extend(receipt.parent / row["path"]
                          for row in loaded["receipt"]["producer_tools"].values())
    for path in evidence_paths:
        _path(str(path), root=receipt.parent)
        context.files.append(ab._path_receipt(path))
    context.trees.append((source, _tree(source, ab)))
    context.trees.append((runtime_root, _tree(runtime_root, ab)))
    context.verify_inputs()
    protected = [receipt.parent, source, runtime_root, Path(loaded["origin_source_root"])]
    protected.extend(Path(row["path"]) for row in context.files + context.tools)
    owner = _new_output(args.output_dir, ab, protected)
    context.output_owner = owner
    output = owner.path
    context.output = output
    manifest_path = output / "capture.json"
    _persist(owner, "capture.json", {"schema": SCHEMA, "status": "PREPARING",
                            "source_promotion_authorized": False})
    native = _copy_owned(context, compiler, "pcc1", mode=0o555)
    context.files.append(ab._path_receipt(native))
    env = ab._measurement_env(runtime, args.gc_backend, host_source_root=source,
        host_python=Path(sys.executable).resolve(strict=True), private_root=output / "private",
        frontend_jobs=1, self_backend_jobs=1, worker_budget=context.budget)
    # No caller/receipt environment overlays. This is a new off-pass capture.
    for key in tuple(env):
        if key.startswith("PCC_DEBUG") or key in ("DEVELOPER_DIR", "SDKROOT", "MACOSX_DEPLOYMENT_TARGET"):
            env.pop(key)
    env.update(PATH="/nonexistent", PCC_HOST_PYTHON="/usr/bin/false",
               PCC_HOST_PCC="/usr/bin/false", PCC_RUNTIME_CC="/usr/bin/false",
               PCC_PYTHON_IR_PASSES="off", PCC_WITH_THREADS="1",
               PCC_PY_FRONTEND_IN_PROCESS_CODEGEN="0")
    for key in ("CAPTURE", "EMIT", "REQUIRE_ZERO_FALLBACK", "FUSE_USES", "RELEASE_FRONTEND"):
        env["PCC_DIRECT_INDEXED_KERNEL_" + key] = "1"
    env["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] = "1"
    commands = [context.run([str(native), "--pcc-native-deferred-worker", "--check"],
                            dict(env), output, output / "capability", args.timeout)]
    plan = output / "capture.pcc-codegen-plan"
    coordinator_env = dict(env, PCC_DEFER_FRONTEND_CODEGEN_PLAN=str(plan),
                           PCC_DEFER_FRONTEND_OUTPUT=str(output / "pcc2"))
    command = [str(native), "--backend", "self", "--ir-scaffold=on",
               str(source / "pcc/__main__.py"), "-o", str(output / "pcc2")]
    commands.append(context.run(command, coordinator_env, source, output / "coordinator", args.timeout))
    _require(not (output / "pcc2").exists(), "coordinator crossed the deferred capture boundary")
    chosen, manifests = _plan(plan, compiler=native, runtime=runtime, source=source,
        source_files=context.source_manifest["files"], output=output, module_name=args.module_name)
    context.trees.append((chosen["ast"], _tree(chosen["ast"], ab, seal=True)))
    for path in [plan, chosen["exports"], *manifests]:
        path.chmod(0o444)
        context.files.append(ab._path_receipt(path))
    worker_root = owner.mkdir("worker")
    artifacts = owner.mkdir("artifacts", directory="worker")
    result_path, worker_manifest = worker_root / "result.tsv", worker_root / "worker.manifest"
    rows = list(chosen["rows"])
    rows[1:3] = [str(result_path), str(artifacts)]
    owner.create("worker.manifest", directory="worker", text="\n".join(rows) + "\n")
    context.files.append(ab._path_receipt(worker_manifest))
    worker_env = dict(env, PCC_DEFER_FRONTEND_CODEGEN_PLAN="", PCC_DEFER_FRONTEND_OUTPUT="",
                      PCC_DEFER_SELF_LINK_PLAN="", PCC_DIRECT_INDEXED_SIDECAR="1")
    commands.append(context.run([str(native), "--pcc-python-multi-codegen-worker", str(worker_manifest)],
                                worker_env, source, output / "frontend", args.timeout))
    _path(str(result_path), root=worker_root)
    result_rows = result_path.read_text(encoding="utf-8").splitlines()
    expected = artifacts / ("module_" + str(chosen["index"]) + ".direct.pidx")
    parts = result_rows[0].split("\t") if len(result_rows) == 1 else []
    ir_path = artifacts / ("module_" + str(chosen["index"]) + ".ll")
    _require(len(parts) in (9, 12)
             and parts[:5] == ["OK", str(chosen["index"]), args.module_name, "0", "0"]
             and parts[6] == str(ir_path) and parts[-2:] == ["PIDX", str(expected)],
             "frontend returned another/partial output")
    if len(parts) == 12:
        for value in parts[7:10]:
            _integer(value)
    _path(str(ir_path), root=artifacts)
    with ir_path.open(encoding="utf-8") as stream:
        characters = sum(len(chunk) for chunk in iter(lambda: stream.read(1024 * 1024), ""))
    _require(_integer(parts[5]) == characters, "frontend IR length differs from its receipt")
    _require({path.name for path in artifacts.iterdir()} == {expected.name, ir_path.name},
             "frontend emitted unexpected artifacts or a backend output")
    header = _pidx_header(expected, args.expected_target)
    corpus = owner.mkdir("corpus")
    captured = _copy_owned(context, expected, expected.name, directory="corpus")
    for command_record in commands:
        context.files.extend((command_record["stdout"], command_record["stderr"]))
    for path in (result_path, ir_path, expected, captured):
        path.chmod(0o444)
        context.files.append(ab._path_receipt(path))
    result = {"schema": SCHEMA, "status": "PREPARED", "source_promotion_authorized": False,
        "qualification": "UNQUALIFIED_INPUT_ONLY", "origin": "NEW_CAPTURE_NATIVE_DEFERRED_COORDINATOR",
        "execution_owner": "native_compiler_with_host_guard_orchestration",
        "compiler_build_receipt": loaded["sha256"], "source_manifest": context.source_manifest,
        "historical_build_external_tools": loaded["external_tools"],
        "historical_build_tools_scope": "identity_in_verified_build_receipt_not_current_capture_dependencies",
        "runtime_bundle": loaded["receipt"]["runtime_bundle"], "guard": context.budget.receipt,
        "tools": context.tools, "commands": commands, "target_header": header,
        "producer_source_admission": "CURRENT_TOOL_CHECKOUT_COMPLETE_BUILD_CLOSURE",
        "effective_python_ir_passes": [], "requested_python_ir_passes": "off", "output_kind": "PCO",
        "effective_policy_evidence": "DERIVED_FROM_ADMITTED_SOURCE_AND_EXACT_ENVIRONMENT",
        "runtime_pass_observation": "NOT_COLLECTED",
        "backend_emission": "NOT_RUN", "performance_measurement": "NOT_RUN",
        "terminal_guard_verdict": "PENDING_CALLER_VERIFICATION", "module_name": args.module_name,
        "module_index": chosen["index"], "dependencies": context.files,
        "dependency_trees": [{"root": str(root), "files": files} for root, files in context.trees]}
    context.verify_inputs()
    inputs = {"schema": "pcc.stage2-object-inputs.v1", "source_bundle_sha256":
              context.source_manifest["bootstrap_source_sha256"], "capture_qualification": "INPUT_ONLY",
              "requires_live_guarded_verification": True,
              "items": [{"index": chosen["index"], "module_name": args.module_name,
                         "path": captured.name, "size_bytes": captured.stat().st_size,
                         "sha256": ab.sha256_path(captured)}]}
    input_manifest = corpus / "manifest.json"
    _persist(owner, "manifest.json", inputs, directory="corpus")
    result["input_manifest"] = ab._path_receipt(input_manifest)
    context.files.append(result["input_manifest"])
    # Keep the result independent of later capability bookkeeping.
    result["dependencies"] = list(context.files)
    _persist(owner, "capture.json", result)
    context.files.append(ab._path_receipt(manifest_path))
    context.verify_inputs()
    return result


def capture_guarded(args, *, ab, performance_lock_token, guard_result_path,
                    expected_guard_command, expected_guard_cwd, campaign_deadline_monotonic):
    """Prepare one fresh same-input corpus under the caller's actual watchdog.

    The caller must have launched that guard and later verify its terminal
    result. A RUNNING JSON file or this result cannot grant CI admission.
    ``args`` supplies compiler/build_receipt/compiler_arm/runtime_archive,
    module_name/output_dir/gc_backend/timeout/expected_target/python_ir_passes.
    """
    _require(Path(ab.__file__).resolve(strict=True) == SUPPORT.resolve(strict=True)
             and ab._validated_worker_budget.__globals__ is vars(ab)
             and ab._run_process.__globals__ is vars(ab)
             and performance_lock_token is not None
             and ab._HELD_PERFORMANCE_LOCK is performance_lock_token
             and ab._ACTIVE_WORKER_BUDGET is None, "capture needs the caller's live support module/lock")
    budget = ab._validated_worker_budget(guard_result_path=guard_result_path,
        expected_guard_command=expected_guard_command, expected_guard_cwd=expected_guard_cwd,
        campaign_deadline_monotonic=campaign_deadline_monotonic)
    context = _Context(ab, performance_lock_token, budget)
    ab._ACTIVE_WORKER_BUDGET = budget
    try:
        context.verify()
        result = _capture(args, context)
        digest = ab._canonical_sha256(result)
        context.verify()
        _LIVE_CAPTURES[id(result)] = (result, context, digest)
        return result
    except BaseException as exc:
        _invalidate(context, exc)
        _close_output(context)
        raise
    finally:
        ab._ACTIVE_WORKER_BUDGET = None


def verify_guarded_capture(result, *, ab, performance_lock_token):
    """Revalidate a live preparation result inside the same guarded campaign."""
    entry = _LIVE_CAPTURES.get(id(result))
    _require(entry is not None and entry[0] is result, "capture has no live producer")
    _, context, digest = entry
    _require(context.ab is ab and context.token is performance_lock_token,
             "capture caller differs from its producer")
    previous = ab._ACTIVE_WORKER_BUDGET
    _require(previous is None or previous is context.budget, "another worker budget is active")
    ab._ACTIVE_WORKER_BUDGET = context.budget
    try:
        _require(ab._canonical_sha256(result) == digest, "capture result changed")
        context.verify_inputs()
    except BaseException as exc:
        _LIVE_CAPTURES.pop(id(result), None)
        _invalidate(context, exc)
        _close_output(context)
        raise
    finally:
        ab._ACTIVE_WORKER_BUDGET = previous

"""Select and pin the Go 1.27.3 standard-library source closure of testing.

Reproduce tests/go/upstream/go1.27.3/pcc-std-closure-lock.json from the
official archive (checksum from https://go.dev/dl/?mode=json&include=all):

    curl -LO https://go.dev/dl/go1.27.3.src.tar.gz   # sha256 6e7749035cf3...
    python -I -c "import tarfile;tarfile.open('go1.27.3.src.tar.gz').extractall('x', filter='data')"
    python -I scripts/pin_go_std_closure.py x/go tests/go/upstream/go1.27.3

Selection: every non-test .go file of each package in the import closure of
ROOTS, chosen by file-name suffix and //go:build line for at least one of
CONTEXTS (cgo and unknown tags false, goexperiment.* either value), stopping
at the compiler-owned packages. Files already in the fixture must be
identical; nothing is rewritten. scripts/go_std_closure.py verifies the pin.
"""
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

COMMIT = "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
ROOTS = ("testing", "fmt", "os", "testing/internal/testdeps")
CONTEXTS = [("linux", "amd64"), ("linux", "arm64"), ("darwin", "amd64"), ("darwin", "arm64")]
KNOWN_OS = set("aix android darwin dragonfly freebsd hurd illumos ios js linux nacl netbsd openbsd plan9 solaris wasip1 windows zos".split())
UNIX = set("aix android darwin dragonfly freebsd hurd illumos ios linux netbsd openbsd solaris".split())
KNOWN_ARCH = set("386 amd64 amd64p32 arm armbe arm64 arm64be loong64 mips mipsle mips64 mips64le mips64p32 mips64p32le ppc ppc64 ppc64le riscv riscv64 s390 s390x sparc sparc64 wasm".split())


def owned(path):
    return path in {"unsafe", "C"} or path == "runtime" or path.startswith("runtime/") or path.startswith("internal/runtime/")


def name_ok(name, goos, goarch):
    stem = name[:-3]
    parts = stem.split("_")
    if len(parts) >= 3 and parts[-2] in KNOWN_OS and parts[-1] in KNOWN_ARCH:
        return parts[-2] == goos and parts[-1] == goarch
    if len(parts) >= 2 and parts[-1] in KNOWN_OS:
        return parts[-1] == goos
    if len(parts) >= 2 and parts[-1] in KNOWN_ARCH:
        return parts[-1] == goarch
    return True


def tokens(expr):
    return re.findall(r"\|\||&&|!|\(|\)|[A-Za-z0-9_.]+", expr)


def evaluate(expr, value):
    toks = tokens(expr)
    pos = 0
    def peek():
        return toks[pos] if pos < len(toks) else None
    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]
    def orx():
        v = andx()
        while peek() == "||":
            take(); w = andx(); v = v or w
        return v
    def andx():
        v = notx()
        while peek() == "&&":
            take(); w = notx(); v = v and w
        return v
    def notx():
        if peek() == "!":
            take(); return not notx()
        if peek() == "(":
            take(); v = orx(); take(); return v
        return value(take())
    return orx()


def constraint(text):
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("package "):
            return None
        if stripped.startswith("//go:build "):
            return stripped[len("//go:build "):]
    return None


def selected(text, goos, goarch):
    expr = constraint(text)
    if expr is None:
        return True
    free = sorted({atom for atom in tokens(expr) if atom.startswith("goexperiment.")})
    for assignment in itertools.product((False, True), repeat=len(free)):
        free_values = dict(zip(free, assignment))
        def value(atom):
            if atom in free_values:
                return free_values[atom]
            if atom in (goos, goarch, "gc"):
                return True
            if atom == "unix":
                return goos in UNIX
            if re.fullmatch(r"go1\.\d+", atom):
                return int(atom.split(".")[1]) <= 27
            return False
        if evaluate(expr, value):
            return True
    return False


def imports(text):
    head = text.split("\nfunc ", 1)[0]
    found = re.findall(r'(?m)^import\s+(?:[\w.]+\s+)?"([^"]+)"', head)
    for block in re.findall(r'(?ms)^import\s*\((.*?)^\)', head):
        found += re.findall(r'"([^"]+)"', block)
    return found



def closure(source_root):
    """{import path: [selected file names]} over an extracted tree's src/."""
    packages, pending = {}, list(ROOTS)
    while pending:
        path = pending.pop()
        if path in packages or owned(path):
            continue
        files = []
        for file in sorted((source_root / path).glob("*.go")):
            if file.name.endswith("_test.go"):
                continue
            text = file.read_text(encoding="utf-8")
            if any(name_ok(file.name, goos, goarch) and selected(text, goos, goarch) for goos, goarch in CONTEXTS):
                files.append(file.name)
                pending.extend(imports(text))
        if not files:
            raise SystemExit("no selected files for " + path)
        packages[path] = files
    return packages


def main(extract, fixture):
    packages = closure(extract / "src")
    entries, total, written, reused = [], 0, 0, 0
    for package in sorted(packages):
        for name in packages[package]:
            path = "src/" + package + "/" + name
            data = (extract / path).read_bytes()
            data.decode("utf-8")
            target = fixture / path
            if target.exists():
                if target.read_bytes() != data:
                    raise SystemExit("existing pinned file differs: " + path)
                reused += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                written += 1
            total += len(data)
            entries.append({
                "path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "git_blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                "url": "https://raw.githubusercontent.com/golang/go/" + COMMIT + "/" + path,
                "license": "BSD-3-Clause", "license_evidence": "LICENSE",
            })
    lock = {
        "schema_version": 1,
        "project": "go-std-test-closure",
        "version": "go1.27.3",
        "repository": "https://github.com/golang/go",
        "revision": COMMIT,
        "tag": "go1.27.3",
        "license": "BSD-3-Clause",
        "license_file": "LICENSE",
        "source_archive": {
            "url": "https://go.dev/dl/go1.27.3.src.tar.gz", "size": 35134035,
            "sha256": "6e7749035cf360f06b2575d162ea79df9fda8ef671889e5c375f8e50777f26bb",
            "checksum_source": "https://go.dev/dl/?mode=json&include=all",
            "commit_check": "tag go1.27.3 resolves to the revision; every src/testing blob and the 12 earlier pinned files match it",
        },
        "roots": list(ROOTS),
        "owned_excluded": ["unsafe", "C", "runtime", "runtime/...", "internal/runtime/..."],
        "build_contexts": [list(item) for item in CONTEXTS],
        "build_tags": "GOOS, GOARCH, unix (linux and darwin), gc and go1.1-go1.27 are true; cgo and every other tag is false; goexperiment.* may take either value",
        "selection": ("Every non-test .go file, selected by file-name suffix and //go:build line for at least one build "
                      "context, of each package in the import closure of the roots, stopping at the compiler-owned "
                      "packages. Assembly (.s), test files, testdata and other packages are not pinned; no file is changed."),
        "complete_build_closure": False,
        "packages": dict(sorted(packages.items())),
        "total_bytes": total,
        "files": entries,
    }
    (fixture / "pcc-std-closure-lock.json").write_text(json.dumps(lock, indent=1, sort_keys=False) + "\n")
    print("written", written, "reused", reused, "files", len(entries), "bytes", total)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))

"""Cross-language DataSketches performance comparison (reference and PCC arms).

    uv run python scripts/datasketches_bench.py --out DIR [--n 10000000] [--rounds 5] [--arms A,B]

One workload (benchmarks/datasketches/workload.*: HLL_8 lg_k 12, Theta lg_k
12 and KLL<double> k 200 over the keys 0..N-1, each timing only its update
loop) runs against the pinned DataSketches sources of every language
(scripts/datasketches_sources.py). Reference arms build with that language's
own toolchain (scripts/datasketches_reference_toolchains.py) and are labeled
baselines only. PCC arms depend on no other compiler: each is compiled,
assembled and linked by PCC alone -- the Python package's C++ extension and
nanobind included -- and no reference tool may build, check or certify one.
An arm that cannot be built is reported UNRUN with the reason, never timed
or substituted. Builds and runs hold build/.pcc-performance.lock; arms
alternate with a rotating start order for --rounds rounds (five or more for a
result; fewer is a screen), and each process's maximum RSS is checked against
--rss-cap. DIR receives the binaries, raw rounds, identities and results.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.datasketches_reference_toolchains import load_receipt  # noqa: E402
from scripts.datasketches_sources import LOCK_SHA256, PROJECTS, ROOT as PROJECT_ROOT, load_project  # noqa: E402
from scripts.file_lock import exclusive_file_lock  # noqa: E402

WORKLOAD = ROOT / "benchmarks" / "datasketches"
LOCK_PATH = ROOT / "build" / ".pcc-performance.lock"
FAMILIES = ("hll8_lg12", "theta_lg12", "kll_double_k200")
RUST_FEATURES = ("bloom", "countmin", "cpc", "frequencies", "hll", "kll", "req", "tdigest", "theta", "tuple")
# The owned frontends' current first blockers (tests/test_datasketches_projects.py).
PCC_FRONTIERS = {
    "cpp-pcc": "owned C++ headers lack <cstdint> (common/include/common_defs.hpp:23)",
    "rust-pcc": "multi-file crate stops at a `..=` range expression argument (datasketches/src/bloom/sketch.rs:486:46)",
    "go-pcc": "instantiated generic type CompareFn[float64] (common/item_sketch_double.go:33:58)",
    "java-pcc": "374 production units exceed the 256-unit source input limit",
    "python-pcc": "nanobind is not pinned (src/datasketches.cpp:20)",
}
ARMS = ("cpp-ref", "rust-ref", "go-ref", "java-ref", "python-ref") + tuple(PCC_FRONTIERS)


class Unrun(Exception):
    """The arm cannot be built here; the message is the recorded reason."""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, *, timeout, cwd=None):
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                               start_new_session=True)
    if completed.returncode:
        raise RuntimeError(" ".join(map(str, command[:3])) + " failed:\n" + completed.stderr[-4000:])
    return completed.stdout


def tool_version(command):
    try:
        return run(command, timeout=60).strip().splitlines()[0]
    except (OSError, RuntimeError, subprocess.TimeoutExpired, IndexError):
        return None


def build_cpp_reference(build):
    if sys.platform == "darwin" and shutil.which("xcrun"):
        # Command Line Tools clang searches a toolchain libc++ directory that
        # may be empty; name the SDK's libc++ headers explicitly.
        sdk = run(["xcrun", "--show-sdk-path"], timeout=60).strip()
        compiler = ["xcrun", "clang++", "-nostdinc++", "-isystem", sdk + "/usr/include/c++/v1"]
    elif shutil.which("clang++"):
        compiler = ["clang++"]
    else:
        raise Unrun("clang++ is not installed")
    root = PROJECT_ROOT / "projects" / PROJECTS["cpp"]
    includes = [f"-I{path}" for path in sorted(root.glob("*/include"))]
    binary = build / "cpp-ref"
    run(compiler + ["-std=c++17", "-O2", *includes, str(WORKLOAD / "workload.cpp"), "-o", str(binary)], timeout=600)
    return binary, {"compiler": tool_version([*compiler[:2], "--version"]), "flags": "-std=c++17 -O2"}


def build_rust_reference(build):
    if shutil.which("rustc") is None:
        raise Unrun("rustc is not installed")
    root = PROJECT_ROOT / "projects" / PROJECTS["rust"] / "datasketches"
    library = build / "rust"
    library.mkdir(exist_ok=True)
    features = [argument for name in RUST_FEATURES for argument in ("--cfg", f'feature="{name}"')]
    run(["rustc", "--edition=2024", "--crate-type=rlib", "--crate-name", "datasketches", "-C", "opt-level=3",
         *features, str(root / "src/lib.rs"), "--out-dir", str(library)], timeout=900)
    binary = build / "rust-ref"
    run(["rustc", "--edition=2024", "-C", "opt-level=3", "--extern",
         f"datasketches={library / 'libdatasketches.rlib'}", str(WORKLOAD / "workload.rs"), "-o", str(binary)],
        timeout=900)
    return binary, {"compiler": tool_version(["rustc", "--version"]), "flags": "-C opt-level=3, all sketch features"}


def receipt():
    """The installed reference toolchains (scripts/datasketches_reference_toolchains.py)."""
    installed = load_receipt()
    if installed is None:
        raise Unrun("reference toolchains are not installed; run scripts/datasketches_reference_toolchains.py")
    return installed


def go_environment(installed, build):
    # GOROOT is explicit: a version manager's exported GOROOT must not win.
    environment = dict(os.environ, GOROOT=installed["go_root"], GOTOOLCHAIN="local", GOENV="off",
                       GOFLAGS="-mod=mod", CGO_ENABLED="0",
                       GOMODCACHE=installed["gomodcache"], GOPATH=str(Path(installed["gomodcache"]).parent / "gopath"),
                       GOCACHE=str(build / "gocache"))
    environment["PATH"] = str(Path(installed["go_root"]) / "bin") + os.pathsep + environment["PATH"]
    return environment


def build_go_reference(build):
    installed = receipt()
    go = str(Path(installed["go_root"]) / "bin/go")
    module = build / "go"
    module.mkdir()
    pinned = PROJECT_ROOT / "projects" / PROJECTS["go"]
    # The pinned module is used in place (replace); its go.sum verifies the
    # murmur3, xxhash and x/exp downloads into the receipt's module cache.
    (module / "go.mod").write_text("module pcc.local/datasketchesbench\n\ngo 1.24.11\n\n"
                                   "require github.com/apache/datasketches-go v0.3.0\n\n"
                                   f"replace github.com/apache/datasketches-go => {pinned}\n")
    shutil.copyfile(pinned / "go.sum", module / "go.sum")
    shutil.copyfile(WORKLOAD / "workload.go", module / "workload.go")
    environment = go_environment(installed, build)
    completed = subprocess.run([go, "build", "-trimpath", "-o", str(build / "go-ref"), "."], cwd=module,
                               env=environment, capture_output=True, text=True, timeout=1800, start_new_session=True)
    if completed.returncode:
        raise RuntimeError("go build failed:\n" + completed.stderr[-4000:])
    return build / "go-ref", {"compiler": installed["versions"]["go"], "flags": "go build -trimpath (default -O)",
                              "go_sum_sha256": sha256(module / "go.sum")}


def build_java_reference(build):
    installed = receipt()
    java_home = Path(installed["java_home"])
    root = PROJECT_ROOT / "projects" / PROJECTS["java"] / "src/main/java"
    classes = build / "java-classes"
    classes.mkdir()
    sources = sorted(str(path) for path in root.rglob("*.java")) + [str(WORKLOAD / "Workload.java")]
    arguments = build / "javac-sources.txt"
    arguments.write_text("\n".join(sources) + "\n")
    run([str(java_home / "bin/javac"), "--release", "25", "-nowarn", "-d", str(classes), f"@{arguments}"], timeout=1800)
    launcher = build / "java-ref"
    launcher.write_text("#!/bin/sh\nexec " + str(java_home / "bin/java") + " -cp " + str(classes) + ' Workload "$@"\n')
    launcher.chmod(0o755)
    return launcher, {"compiler": installed["versions"]["java"], "flags": "javac --release 25; default JVM flags"}


def build_python_reference(build):
    """The unchanged package built by its own setup.py/CMake with the receipt's tools.

    Only the build copy's ExternalProject is redirected: its C++ core comes
    from the pinned datasketches-cpp copy instead of a git clone, and it
    installs below the build directory instead of upstream's fixed
    /tmp/datasketches. No C++ or binding source changes.
    """
    installed = receipt()
    python = Path(installed["python"])
    source = build / "python-src"
    shutil.copytree(PROJECT_ROOT / "projects" / PROJECTS["python"], source, symlinks=True)
    core = build / "cpp-src"
    shutil.copytree(PROJECT_ROOT / "projects" / PROJECTS["cpp"], core, symlinks=True)
    cmake = source / "CMakeLists.txt"
    text = cmake.read_text()
    original = ("  GIT_REPOSITORY https://github.com/apache/datasketches-cpp.git\n  GIT_TAG 5.2.0\n"
                "  GIT_SHALLOW true\n  GIT_SUBMODULES \"\"\n")
    if text.count(original) != 1 or text.count("/tmp/datasketches") != 2:
        raise RuntimeError("datasketches-python CMakeLists.txt no longer has the expected ExternalProject")
    text = text.replace(original, f"  SOURCE_DIR {core}\n").replace("/tmp/datasketches", str(build / "cpp-install"))
    cmake.write_text(text)
    environment = dict(os.environ)
    environment["PATH"] = str(python.parent) + os.pathsep + environment["PATH"]
    if sys.platform == "darwin":
        sdk = run(["xcrun", "--show-sdk-path"], timeout=60).strip()
        environment["CXXFLAGS"] = f"-nostdinc++ -isystem {sdk}/usr/include/c++/v1"
    wheels = build / "python-wheel"
    uv = shutil.which("uv")
    completed = subprocess.run([uv, "build", "--wheel", "--no-build-isolation", "--python", str(python),
                                "--out-dir", str(wheels), str(source)], env=environment, capture_output=True,
                               text=True, timeout=3600, start_new_session=True)
    if completed.returncode:
        raise RuntimeError("python package build failed:\n" + (completed.stdout + completed.stderr)[-4000:])
    wheel, = wheels.glob("*.whl")
    site = build / "python-site"
    run([uv, "pip", "install", "--python", str(python), "--no-deps", "--target", str(site), str(wheel)], timeout=600)
    launcher = build / "python-ref"
    launcher.write_text(f"#!/bin/sh\nPYTHONPATH={site} exec {python} {WORKLOAD / 'workload.py'} \"$@\"\n")
    launcher.chmod(0o755)
    return launcher, {"compiler": installed["versions"]["python"] + ", " + ", ".join(installed["python_requirements"]),
                      "flags": "upstream setup.py/CMake Release; ExternalProject redirected to the pinned C++ copy",
                      "wheel": wheel.name, "wheel_sha256": sha256(wheel)}


def unavailable_reference(arm):
    raise Unrun(PCC_FRONTIERS[arm])


BUILDERS = {"cpp-ref": build_cpp_reference, "rust-ref": build_rust_reference, "go-ref": build_go_reference,
            "java-ref": build_java_reference, "python-ref": build_python_reference}


def parse_output(text, n):
    rows = {}
    for line in text.splitlines():
        row = json.loads(line)
        if row["n"] != n or row["family"] in rows:
            raise ValueError("unexpected workload output: " + line)
        rows[row["family"]] = row
    if tuple(rows) != FAMILIES:
        raise ValueError("workload families differ: " + ", ".join(rows))
    return rows


def measure(binary, n, rss_cap):
    """One process: workload rows plus its maximum resident set (bytes)."""
    if sys.platform == "darwin":
        command, pattern, scale = ["/usr/bin/time", "-l", str(binary), str(n)], r"(\d+)\s+maximum resident set size", 1
    else:
        command, pattern, scale = ["/usr/bin/time", "-v", str(binary), str(n)], r"Maximum resident set size \(kbytes\): (\d+)", 1024
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600, start_new_session=True)
    if completed.returncode:
        raise RuntimeError(f"{binary.name} exited {completed.returncode}: {completed.stderr[-2000:]}")
    found = re.search(pattern, completed.stderr)
    rss = int(found.group(1)) * scale if found else None
    if rss is None or rss > rss_cap:
        raise RuntimeError(f"{binary.name} maximum RSS {rss} exceeds the {rss_cap}-byte cap or was not reported")
    return parse_output(completed.stdout, n), rss


def summarize(rounds, built):
    """Median ns/update per arm and family, pair wins against the first arm."""
    summary = {}
    for arm in built:
        per_family = {}
        for family in FAMILIES:
            samples = [entry[arm]["rows"][family]["seconds"] for entry in rounds]
            n = rounds[0][arm]["rows"][family]["n"]
            per_family[family] = {"median_ns_per_update": statistics.median(samples) * 1e9 / n,
                                  "samples": len(samples),
                                  "result": rounds[0][arm]["rows"][family]["result"]}
        summary[arm] = per_family
    if len(built) > 1:
        control = built[0]
        for arm in built[1:]:
            for family in FAMILIES:
                wins = sum(entry[arm]["rows"][family]["seconds"] < entry[control]["rows"][family]["seconds"]
                           for entry in rounds)
                summary[arm][family]["wins_against_" + control] = wins
    return summary


def table(summary, unrun):
    lines = ["arm         " + "".join(f"{family:>20}" for family in FAMILIES) + "   (median ns/update)"]
    for arm, per_family in summary.items():
        lines.append(f"{arm:<12}" + "".join(f"{per_family[family]['median_ns_per_update']:>20.2f}" for family in FAMILIES))
    lines.extend(f"{arm:<12}  UNRUN: {reason}" for arm, reason in unrun.items())
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=10_000_000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--rss-cap", type=int, default=1 << 30)
    arguments = parser.parse_args(argv)
    arms = [arm for arm in arguments.arms.split(",") if arm]
    if not set(arms) <= set(ARMS) or arguments.n < 1 or arguments.rounds < 1:
        parser.error("unknown arm, or nonpositive --n/--rounds")
    arguments.out.mkdir(parents=True, exist_ok=False)
    build = arguments.out / "build"
    build.mkdir()
    for key in PROJECTS:
        load_project(key)
    identity = {"host": platform.platform(), "machine": platform.machine(), "n": arguments.n,
                "rounds": arguments.rounds, "source_locks": dict(LOCK_SHA256),
                "workloads": {path.name: sha256(path) for path in sorted(WORKLOAD.iterdir()) if path.is_file()},
                "arms": {}}
    built, unrun, rounds = [], {}, []
    with exclusive_file_lock(LOCK_PATH):
        for arm in arms:
            try:
                binary, details = (BUILDERS[arm](build) if arm in BUILDERS else unavailable_reference(arm))
            except Unrun as reason:
                unrun[arm] = str(reason)
                continue
            identity["arms"][arm] = {**details, "binary_sha256": sha256(binary)}
            built.append((arm, binary))
        for index in range(arguments.rounds):
            order = built[index % len(built):] + built[:index % len(built)] if built else []
            entry = {}
            for arm, binary in order:
                rows, rss = measure(binary, arguments.n, arguments.rss_cap)
                entry[arm] = {"rows": rows, "max_rss_bytes": rss}
            rounds.append(entry)
            print(f"round {index + 1}/{arguments.rounds} done", flush=True)
    summary = summarize(rounds, [arm for arm, _ in built]) if built else {}
    result = {"identity": identity, "summary": summary, "unrun": unrun, "rounds": rounds,
              "screen_only": arguments.rounds < 5}
    (arguments.out / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(table(summary, unrun))
    if arguments.rounds < 5:
        print("fewer than five rounds: a screen, not a result")
    return 0


if __name__ == "__main__":
    sys.exit(main())

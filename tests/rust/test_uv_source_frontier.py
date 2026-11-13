"""uv 0.13.7 source frontier: pinned originals and each Rust root's first blocker.

The pinned roots (projects/uv-0.13.7, MIT OR Apache-2.0) are unchanged upstream
files. A root's recorded blocker is updated when the frontend advances past it;
a change elsewhere must not silently move it. No Cargo, build script, macro or
upstream test is executed, and no uv build is claimed.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "uv-0.13.7"
LOCK_SHA256 = "c6ea2cf2f3acdd33fa8aee8673adc48e7bcfd9ee7779ad0ab7e9b41090f1d60c"
REVISION = "316c827e058c16b71d5c507a20729c2995a7bcce"
TARGET = "x86_64-unknown-linux-gnu"

FRONTIER = (
    ("crates/uv/src/bin/uv.rs", "3:3: cfg requires an actual source/options-bound CheckedRustSource configuration"),
    # `@` patterns and the tool lint paths in
    # `#![deny(clippy::print_stdout, clippy::print_stderr)]` are owned; line 7
    # is `#[cfg(feature = "self-update")]`.
    ("crates/uv/src/lib.rs", "7:3: cfg requires an actual source/options-bound CheckedRustSource configuration"),
    ("crates/uv-version/src/lib.rs", "5:5: macro invocations require macro-by-example expansion"),
    # Grouped use trees, `pub use` re-exports and `pub(crate) mod export;` now
    # parse; line 94 is `#[cfg(test)] mod windows_emulation_tests;`.
    ("crates/uv-lock/src/lock/mod.rs", "94:3: cfg requires an actual source/options-bound CheckedRustSource configuration"),
)


def test_pinned_uv_originals_match_their_source_lock():
    from scripts.ci_source_frontier_support import load_source_lock
    lock, files = load_source_lock(PROJECT, LOCK_SHA256)
    assert (lock["project"], lock["version"], lock["revision"]) == ("uv", "0.13.7", REVISION)
    assert lock["license"] == "MIT OR Apache-2.0" and lock["edition"] == 2024
    assert tuple(lock["rust_roots"]) == tuple(name for name, _ in FRONTIER)
    assert len(files) == 12 and lock["total_bytes"] == 829301
    import tomllib
    workspace = tomllib.loads(files["Cargo.toml"].decode())["workspace"]["package"]
    assert (workspace["edition"], workspace["rust-version"], workspace["license"]) == (
        "2024", "1.97.0", "MIT OR Apache-2.0")
    python = tomllib.loads(files["pyproject.toml"].decode())
    assert python["project"]["version"] == "0.13.7" and python["build-system"]["build-backend"] == "maturin"


@pytest.mark.parametrize("name,blocker", FRONTIER, ids=[name for name, _ in FRONTIER])
def test_uv_rust_root_stops_at_its_recorded_first_blocker(name, blocker):
    from pcc.frontends.rust import RustError, compile_rust
    from pcc.frontends.rust.syntax import Parser
    source = (PROJECT / name).read_text(encoding="utf-8")
    try:
        Parser(source, source_path=name, edition=2024).parse()
        compile_rust(source, module_name="uv_root", target_triple=TARGET, overflow_checks=False, edition=2024)
    except RustError as error:
        assert blocker in str(error)
    else:
        pytest.fail(name + " passed its recorded blocker; record the next one")

"""uv 0.13.7 source frontier: pinned originals and each Rust root's first blocker.

The pinned roots (projects/uv-0.13.7, MIT OR Apache-2.0) are unchanged upstream
files. A root's recorded blocker is updated when the frontend advances past it;
a change elsewhere must not silently move it. cfg is evaluated by PCC's owned
configuration for x86_64-unknown-linux-gnu with the cfg inputs `cargo build
--release` gives each package (its default features and the workspace release
profile), computed from the pinned manifests. No Cargo, build script, macro or
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
    # Default features enable performance-memory-allocator, so the cfg-gated
    # `extern crate uv_performance_memory_allocator;` is active.
    ("crates/uv/src/bin/uv.rs", "4:14: external crate dependency loading is not implemented"),
    # `@` patterns, tool lint paths and feature cfg (self-update is off) are
    # owned; line 70 has the lifetime `'a`.
    ("crates/uv/src/lib.rs", "70:75: unsupported Rust type \"'a\"; references and traits are not implemented"),
    ("crates/uv-version/src/lib.rs", "5:5: macro invocations require macro-by-example expansion"),
    # Grouped uses, `pub use`, `pub(crate)` and `#[cfg(test)]` (off) are owned;
    # line 98 is `const VERSION: u32 = 1;`.
    ("crates/uv-lock/src/lock/mod.rs", "98:1: expected 'fn', found 'const'"),
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


def release_cfg(name):
    """The cfg inputs of `cargo build --release` for the root's package."""
    import tomllib
    from pcc.frontends.rust.cargo_config import cargo_cfg_options
    workspace = tomllib.loads((PROJECT / "Cargo.toml").read_text())
    package = name.split("/src/", 1)[0]
    manifest = PROJECT / package / "Cargo.toml"
    if not manifest.is_file():
        # crates/uv-lock/Cargo.toml is not pinned; this root's only cfg
        # predicate is `test`, so its features cannot change the result.
        import re
        source = (PROJECT / name).read_text(encoding="utf-8")
        assert set(re.findall(r"#\[cfg\((.*?)\)\]", source)) == {"test"}
        return cargo_cfg_options({}, workspace_manifest=workspace, profile="release")[0]
    return cargo_cfg_options(tomllib.loads(manifest.read_text()), workspace_manifest=workspace,
                             profile="release")[0]


@pytest.mark.parametrize("name,blocker", FRONTIER, ids=[name for name, _ in FRONTIER])
def test_uv_rust_root_stops_at_its_recorded_first_blocker(name, blocker):
    from pcc.frontends.rust import RustError, compile_rust
    from pcc.frontends.rust.configuration import OwnedRustConfiguration
    from pcc.frontends.rust.syntax import Parser
    source = (PROJECT / name).read_text(encoding="utf-8")
    cfg = release_cfg(name)
    try:
        Parser(source, source_path=name, edition=2024,
               configuration=OwnedRustConfiguration(TARGET, 2024, cfg)).parse()
        compile_rust(source, module_name="uv_root", target_triple=TARGET, overflow_checks=False,
                     edition=2024, cfg=cfg)
    except RustError as error:
        assert blocker in str(error)
    else:
        pytest.fail(name + " passed its recorded blocker; record the next one")

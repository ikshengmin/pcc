"""Multi-file crates through owned sema and lowering (compile_rust_crate).

Step 1 of pcc/frontends/rust/README.md, "Plan: the DataSketches Rust arm".
Rust Reference items/modules.html (outlined `mod name;` as `name.rs` or
`name/mod.rs`), paths.html (`crate::`, `super::`, `self::`) and
visibility-and-privacy.html: a supplied source map is loaded without
flattening its scopes (crate_context), resolved per module
(module_resolution.ModuleResolution) and checked and lowered by the same
pipeline as compile_rust, with owned admission only. Module-scoped impls and
traits keep an explicit boundary (steps 1b and 1c). Every crate is original;
results and verdicts were compared with a local rustc 1.92.0 build.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust_crate
from tests.rust.str_execution import UNAVAILABLE, run_owned_crate_u64_function, run_rustc_crate_function

PROGRAM = {
    'src/lib.rs': '''\
mod hash;
pub mod stats;
mod util;

use stats::Summary;
use util::bits::{ones, LOW};

pub fn answer() -> u64 {
    let mut bad = 0u64;
    let s = stats::summarize(hash::mix(7), 3);
    if total(s) != 3 * 0x9ED6 + 6 { bad |= 1 << 0; }
    if ones(0xF0F0) != 8 || LOW != 0xFF { bad |= 1 << 1; }
    if util::bits::low_byte(0x1234) != 0x34 { bad |= 1 << 2; }
    if crate::stats::SALT != 0x9e37 || util::depth() != 2 { bad |= 1 << 3; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}

fn total(s: Summary) -> u64 { s.sum + s.count }
''',
    'src/hash.rs': 'pub fn mix(x: u64) -> u64 { (x << 5) ^ (x >> 2) ^ crate::stats::SALT }\n',
    'src/stats.rs': '''\
pub const SALT: u64 = 0x9e37;

pub struct Summary { pub sum: u64, pub count: u64 }

pub fn summarize(seed: u64, n: u64) -> Summary {
    let mut sum = 0;
    for i in 0..n { sum += seed + i; }
    Summary { sum, count: n }
}
''',
    'src/util/mod.rs': '''\
pub mod bits;

pub fn depth() -> u64 { bits::depth() + 1 }
''',
    'src/util/bits.rs': '''\
pub const LOW: u64 = 0xFF;

pub fn low_byte(x: u64) -> u64 { x & LOW }

pub fn ones(mut x: u64) -> u64 {
    let mut n = 0;
    while x != 0 { n += x & 1; x >>= 1; }
    n
}

pub(super) fn depth() -> u64 { 1 }
''',
}


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_multi_file_crate_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_crate_u64_function('src/lib.rs', PROGRAM, 'answer', tmp_path, monkeypatch,
                                         crate_name='demo')
    reference = run_rustc_crate_function('src/lib.rs', PROGRAM, 'answer', tmp_path)
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


def test_module_functions_get_their_crate_paths_as_symbol_names():
    result = compile_rust_crate('src/lib.rs', PROGRAM, crate_name='demo')
    assert {'answer', 'total', 'hash::mix', 'stats::summarize', 'util::depth', 'util::bits::ones'} <= set(result.symbols)
    assert not result.externally_checked


def crate(lib, **files):
    return {'src/lib.rs': lib, **{f'src/{name}.rs': text for name, text in files.items()}}


# (name, original crate, verdict of a rustc 1.92.0 build).
CORPUS = (
    ('private-function', crate('mod m;\npub fn g() -> u64 { m::f() }', m='fn f() -> u64 { 1 }'), 'E0603'),
    ('private-field', crate('mod m;\npub fn g() -> u64 { m::make().a }',
                            m='pub struct S { a: u64 }\npub fn make() -> S { S { a: 1 } }'), 'E0616'),
    ('private-struct-literal', crate('mod m;\npub fn g() -> u64 { let s = m::S { a: 1 }; 0 }',
                                     m='pub struct S { a: u64 }'), 'E0451'),
    ('unresolved-path', crate('mod m;\npub fn g() -> u64 { m::missing() }', m='pub fn f() -> u64 { 1 }'), 'E0425'),
    ('super-private-ok', crate('mod m;\nfn helper() -> u64 { 2 }\npub fn g() -> u64 { m::f() }',
                               m='pub fn f() -> u64 { super::helper() }'), 'ok'),
    ('pub-crate-ok', crate('mod m;\npub fn g() -> u64 { m::f() }', m='pub(crate) fn f() -> u64 { 1 }'), 'ok'),
    ('use-reexport-ok', crate('mod m;\npub use m::f;\npub fn g() -> u64 { f() }', m='pub fn f() -> u64 { 1 }'), 'ok'),
)
MESSAGES = {
    'E0603': 'private',
    'E0616': 'private',
    'E0451': 'private',
    'E0425': 'unresolved|cannot find|not found|unknown',
}


@pytest.mark.parametrize('name,sources,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_crate_verdicts_match_a_rustc_build_without_running_rustc(name, sources, verdict, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned admission ran a tool'))
    if verdict == 'ok':
        assert not compile_rust_crate('src/lib.rs', sources, crate_name='demo').externally_checked
    else:
        with pytest.raises(RustError, match=MESSAGES[verdict]):
            compile_rust_crate('src/lib.rs', sources, crate_name='demo')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_builds_give_the_same_verdicts(tmp_path):
    for index, (name, sources, verdict) in enumerate(CORPUS):
        root = tmp_path / f'case{index}'
        for path, text in sources.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--crate-name=demo', '--edition=2021',
                              '--emit=metadata', '-A', 'warnings', str(root / 'src/lib.rs'),
                              '-o', str(root / 'demo.rmeta')], capture_output=True, text=True, timeout=60)
        codes = ','.join(sorted(set(re.findall(r'error\[(E\d+)\]', run.stderr))))
        assert ('ok' if run.returncode == 0 else codes) == verdict, (name, run.stderr)


@pytest.mark.parametrize('sources,boundary', [
    (crate('mod m;\npub fn g() -> u64 { m::S { a: 1 }.get() }',
           m='pub struct S { pub a: u64 }\nimpl S { pub fn get(&self) -> u64 { self.a } }'),
     'module-scoped trait and inherent method resolution is not implemented'),
    (crate('mod m;\npub fn g() -> u64 { 1 }'), r'module source missing from supplied closure: src/m\.rs or src/m/mod\.rs'),
])
def test_crate_forms_beyond_step_one_keep_explicit_boundaries(sources, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust_crate('src/lib.rs', sources, crate_name='demo')

"""Retain the original enum-rematch false admission and rustc E0382 evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError
from tests.rust.test_rust_payloads import PREFIX, RETAINED_ENUM_REMATCH, compile_source

ORIGINAL = Path(__file__).parent / 'programs/payload_partial_moves.rs'
ORIGINAL_SHA256 = '404927a0acacc72ecddebb704437b275093388f9239b6504bc1e889cdca0595b'


def original_source():
    data = ORIGINAL.read_bytes()
    assert hashlib.sha256(data).hexdigest() == ORIGINAL_SHA256
    return data.decode()


@pytest.mark.parametrize('source', [
    RETAINED_ENUM_REMATCH,
    PREFIX + 'fn f(e:E){match e{E::None=>(),E::Pair(p,_)=>take(p)};match e{E::None=>(),E::Pair(_,_)=>()};}',
    PREFIX + 'fn f(e:E){let x=(e,7);match x{(E::None,_)=>(),(E::Pair(p,_),_)=>take(p)};match x{(E::None,_)=>(),(E::Pair(_,q),_)=>take(q)};}',
])
def test_discriminant_inspection_requires_its_owning_enum_to_be_initialized(source, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('moved enum reached IR'))
    with pytest.raises(RustError, match='moved'):
        compile_source(source)


def test_exact_former_native_original_is_rejected_before_lowering(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid retained original reached IR'))
    with pytest.raises(RustError, match='moved'):
        compile_source(original_source())


@pytest.mark.parametrize('source', [
    PREFIX + 'fn f(e:E){match e{E::None=>(),E::Pair(p,_)=>take(p)};match e{_=>()};}',
    PREFIX + 'fn f(e:E){let x=(e,P{x:7});match x{(E::None,_)=>(),(E::Pair(p,_),_)=>take(p)};match x{(_,p)=>take(p)};}',
    PREFIX + 'fn f(mut e:E){match e{E::None=>(),E::Pair(p,_)=>take(p)};e=E::Pair(P{x:3},P{x:4});match e{E::None=>(),E::Pair(p,q)=>{take(p);take(q);}}}',
])
def test_wildcards_independent_tuple_fields_and_whole_reinitialization_remain_valid(source):
    compile_source(source)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('case', ['original', 'former-positive'])
def test_development_only_rustc_rejects_same_retained_enum_rematch(case, tmp_path):
    source = ORIGINAL if case == 'original' else tmp_path / 'former_positive.rs'
    original = original_source().encode() if case == 'original' else RETAINED_ENUM_REMATCH.encode()
    if case != 'original':
        source.write_bytes(original)
    output = tmp_path / 'rejected.rmeta'
    result = subprocess.run([
        shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
        '-C', 'overflow-checks=off', str(source), '-o', str(output),
    ], capture_output=True, timeout=15)
    (tmp_path / 'rustc-stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'rustc-stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'rustc-rejection.json').write_text(json.dumps({
        'case': case, 'source_sha256': hashlib.sha256(original).hexdigest(),
        'returncode': result.returncode, 'stdout_hex': result.stdout.hex(),
        'stderr_hex': result.stderr.hex(), 'oracle': shutil.which('rustc'),
    }, indent=2) + '\n')
    assert source.read_bytes() == original
    assert result.returncode != 0 and b'error[E0382]' in result.stderr
    assert b'use of partially moved value' in result.stderr and not output.exists()

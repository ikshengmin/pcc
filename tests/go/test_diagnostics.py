"""Stable capability codes are not a replacement for Go validity checking.

Go specification: https://go.dev/ref/spec#Declarations_and_scope
https://go.dev/ref/spec#Types https://go.dev/ref/spec#Constants
https://go.dev/ref/spec#Run_time_panics https://go.dev/ref/spec#For_range
Each case is a complete original source file; a code names the first recognized
capability boundary, not a claim that the remainder of the program was checked.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.diagnostics import UNIMPLEMENTED_FEATURES
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units


def original(declarations='', body=''):
    return 'package main;\n' + declarations + '\nfunc main(){' + body + '}\n'


CASES = [
    ('package-initialization', original('func init(){}')),
    ('package-initialization', original('func f()int{return 1};var x=f()')),
    ('dot-imports', 'package main;import . "local";func main(){}'),
    ('slice-storage', original('func f(x []int){}')),
    ('local-type-declarations', original(body='type T int')),
    ('embedded-fields', original('type Base int;type T struct{Base}')),
    ('struct-tags', original('type T struct{x int `tag`}')),
    ('pointer-receivers', original('type T int;func(t *T)f(){}')),
    ('predeclared-shadowing', original('var int=3')),
    ('blank-type-declarations', original('type _ int')),
    ('blank-function-declarations', original('func _(){}')),
    ('blank-package-variables', original('var _=3')),
    ('blank-method-declarations', original('type T int;func(t T)_(){}')),
    ('blank-struct-fields', original('type T struct{_ int}')),
    ('interface-types', original('func f(x any){}')),
    ('floating-types', original('func f(x float64){}')),
    ('complex-types', original('func f(x complex128){}')),
    ('array-element-types', original('var x [1]string')),
    ('array-element-types', original('var x [1][1]int')),
    ('array-element-types', original('type T struct{x int};var x [1]T')),
    ('method-values', original('type T int;func(t T)f(){}', 'var t T;_=t.f')),
    ('dynamic-string-conversion', original('func f(x int)string{return string(x)}')),
    ('dynamic-string-concatenation', original('func f(a,b string)string{return a+b}')),
    ('division-panic', original('func f(a,b int)int{return a/b}')),
    ('negative-shift-panic', original('func f(a,b int)int{return a<<b}')),
    ('string-bounds-panic', original('func f(a string,i int)byte{return a[i]}')),
    ('array-bounds-panic', original('func f(a [2]int,i int)int{return a[i]}')),
    ('aggregate-comparison', original(body='var a [1]int;_=a==a')),
    ('aggregate-switch', original(body='var a [1]int;switch a{case a:}')),
    ('non-array-range', original(body='for range 3{}')),
    ('non-array-range', original(body='for range "abc"{}')),
    ('limit-function-arity', original('func f('+','.join('a'+str(i)+' int' for i in range(257))+'){}')),
    ('limit-string-bytes', original('const x="'+'a'*(1048576+1)+'"')),
    ('limit-constant-precision', original('const x='+str(1<<4097))),
    ('limit-type-expansion', original(';'.join('type T'+str(i)+' T'+str(i+1) for i in range(34))+';type T34 int')),
    ('limit-array-elements', original('var a [4097]int')),
    ('limit-struct-fields', original('type T struct{'+ ';'.join('x'+str(i)+' int' for i in range(257))+'}')),
    ('limit-record-layout', original('type T struct{a [4096]int;b int}')),
]


@pytest.mark.parametrize('suffix,source', CASES, ids=[name+'-'+str(i) for i,(name,_) in enumerate(CASES)])
def test_capability_codes_are_explicit_source_located_and_fail_before_ir(suffix, source, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unimplemented capability reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source,filename='original.go')
    error=caught.value;code='go.unsupported.'+suffix
    assert isinstance(error,GoDiagnostic) and error.kind=='unsupported'
    assert error.code==code and error.feature_id==UNIMPLEMENTED_FEATURES[code]
    assert error.filename=='original.go' and error.line>=1 and error.column>=1
    assert str(error).endswith('['+code+']') and error.message in str(error)


@pytest.mark.parametrize('source', [
    original('type init int'), original(body='_="x"-"y"'),
    original(body='_=1[0]'), original(body='for range true{}'),
    original('func f()(int,int){return 1,2}', 'for range f(){}'),
    original(body='_=missing'), original(body='_=1/0'),
    original(body='_=1<<-1'), original('var x comparable'),
    original(body='@'), original(body='if {}'),
])
def test_invalid_source_is_not_labeled_as_an_unimplemented_feature(source):
    with pytest.raises(GoDiagnostic) as caught:compile_go(source)
    assert not isinstance(caught.value,UnsupportedGo)
    assert caught.value.code is None and caught.value.feature_id is None and caught.value.kind=='unclassified'


def test_uncoded_legacy_capability_is_not_misclassified_as_invalid_source():
    # panic is a valid builtin; its runtime contract is not in this code batch.
    with pytest.raises(GoDiagnostic, match='unsupported builtin panic') as caught:
        compile_go(original(body='panic("unimplemented runtime")'))
    assert not isinstance(caught.value, UnsupportedGo)
    assert caught.value.code is None and caught.value.kind == 'unclassified'


def test_import_capability_codes_are_file_graph_boundaries():
    for alias,suffix in [('_','package-initialization'),('int','predeclared-shadowing')]:
        packages={'main':(('main.go','package main;import '+alias+' "local";func main(){}'),),
                  'local':(('lib.go','package local;const X=3'),)}
        with pytest.raises(UnsupportedGo) as caught:compile_go_packages(packages)
        assert caught.value.code=='go.unsupported.'+suffix


def test_registry_has_no_unused_codes_and_every_code_has_one_canonical_feature():
    expected={'go.unsupported.'+name for name,_ in CASES}
    assert expected==set(UNIMPLEMENTED_FEATURES)
    assert len(set(UNIMPLEMENTED_FEATURES.values()))==len(UNIMPLEMENTED_FEATURES)
    with pytest.raises(ValueError,match='unknown Go capability'):
        UnsupportedGo(None,'invented.code','not a known capability')


def test_registry_and_feature_ledger_are_bijective_and_source_hash_bound():
    root=Path(__file__).resolve().parents[2]
    ledger=json.loads((root/'specs/features/go.json').read_text())
    records={row['code']:row for row in ledger['diagnostics']['codes']}
    features={row['id']:row for row in ledger['features']}
    assert set(records)==set(UNIMPLEMENTED_FEATURES)
    for code,feature in UNIMPLEMENTED_FEATURES.items():
        row=records[code];assert row['feature_id']==feature
        assert features[feature]['status']=='explicitly_unimplemented'
        assert code in features[feature]['diagnostics'] and features[feature]['chapter_ids']
        assert hashlib.sha256((root/row['source']['path']).read_bytes()).hexdigest()==row['source']['sha256']


@pytest.mark.integration
def test_complete_diagnostic_control_has_independent_full_width_native_result(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact=compile_owned(units(('diagnostic_control.go',)),observe_entry=('Value',()))
    binary=tmp_path/'control-native';write_artifact(artifact,binary);actual=run(binary)
    raw=(0xfedcba9876543210).to_bytes(8,'little')
    (tmp_path/'raw-diagnostic-observation.json').write_text(json.dumps({'source_sha256':hashlib.sha256(units(('diagnostic_control.go',))[0][1].encode()).hexdigest(),'executable_sha256':hashlib.sha256(artifact.executable).hexdigest(),'stdout_hex':actual.stdout.hex(),'stderr_hex':actual.stderr.hex(),'returncode':actual.returncode,'expected_hex':raw.hex()},indent=2)+'\n')
    assert actual==Execution(0,raw,b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_same_complete_control_program_matches_official_raw_process_streams(tmp_path, monkeypatch):
    originals=units(('diagnostic_control.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(originals)
    native=tmp_path/'control-main';write_artifact(artifact,native)
    reference=compile_reference(originals,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    values=[]
    for owner,binary in [('pcc',native),('reference',reference)]:
        actual=run(binary);values.append(actual)
        (tmp_path/(owner+'.stdout')).write_bytes(actual.stdout);(tmp_path/(owner+'.stderr')).write_bytes(actual.stderr)
        (tmp_path/(owner+'.execution.json')).write_text(json.dumps({'binary':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'returncode':actual.returncode,'stdout_hex':actual.stdout.hex(),'stderr_hex':actual.stderr.hex(),'input_sha256':hashlib.sha256(originals[0][1].encode()).hexdigest()},indent=2)+'\n')
    assert values[0]==values[1]==Execution(0,b'',b'')
    assert (reference.parent/'diagnostic_control.go').read_bytes()==originals[0][1].encode()

"""One index is not a type-argument list merely because it has a final comma.

https://go.dev/ref/spec#Index_expressions
https://go.dev/ref/spec#Instantiations https://go.dev/ref/spec#Order_of_evaluation
CI Go 1.24.13 accepted the exact retained original below. Repairs and new
execution/reference obligations here are locally UNRUN, not observed passes.
"""
import hashlib
import os

import pytest

from pcc.frontends.go import compile_go
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_nullable_pointers import record_pair


# Preserve bytes and the ordinary indexed operand from the original failed
# rejection obligation; do not replace it with a generic instantiation.
ORIGINAL = 'package main;func main(){a:=[1]int{1};_=a[0,]}'
OBSERVABLE = '''package main
var trace uint64
func base() [1]uint64 { trace = trace*10+1; return [1]uint64{17} }
func index() uint64 { trace = trace*10+2; return 3 }
func Id[T any](x T) T { return x }
func Result() uint64 {
    trace = 0
    value := base()[index()&0,]
    array := [1]uint64{0xfedcba9876543210}
    p := &array
    if p[0,] != 0xfedcba9876543210 || "ab"[1,] != 98 { return 0 }
    array[0,] = Id[uint64,](19)
    if array[0,] != 19 { return 0 }
    return trace*100 + value
}
func main() { if Result() != 1217 { for {} } }
'''


@pytest.mark.parametrize('original', [ORIGINAL, OBSERVABLE])
def test_single_trailing_comma_keeps_index_semantics(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.integration
@pytest.mark.parametrize('original,observe,expected', [
    (ORIGINAL, None, b''), (OBSERVABLE, None, b''),
    (OBSERVABLE, ('Result', ()), (1217).to_bytes(8, 'little')),
])
def test_index_comma_originals_execute_owned(original, observe, expected, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('original.go', original),), observe_entry=observe)
    binary = tmp_path/'owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, expected, b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', [ORIGINAL, OBSERVABLE])
def test_official_go_executes_the_identical_trailing_comma_original(original, tmp_path, monkeypatch):
    originals = (('original.go', original),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path/'owned'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path/'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(original.encode()).hexdigest(), binary, reference)
    assert (reference.parent/'original.go').read_bytes() == original.encode()

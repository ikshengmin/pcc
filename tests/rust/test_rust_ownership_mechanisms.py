"""Real Rust adapter consumers of shared structural places and may-flow.

Rust Reference chapters:
https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types
https://doc.rust-lang.org/reference/expressions/loop-expr.html
https://doc.rust-lang.org/reference/expressions/field-expr.html#borrowing
No general borrowing proof or changed source-language admission is added here.
"""
import pytest

from pcc.analysis import ownership
from pcc.frontends.rust import RustError, compile_rust, moves


def test_rust_while_uses_shared_solver_and_preserves_moved_backedge(monkeypatch):
    assert moves.solve_loop is ownership.solve_loop
    observed = []

    def solve(entry, transfer):
        def visit(header):
            observed.append(ownership.clone(header))
            return transfer(header)
        return ownership.solve_loop(entry, visit)

    monkeypatch.setattr(moves, "solve_loop", solve)
    source = "struct P{x:i32} fn take(p:P){} fn f(){let p=P{x:1}; while true {take(p);}}"
    with pytest.raises(RustError, match="use of moved or possibly moved value 'p'"):
        compile_rust(source, experimental_rustc_checked=True)
    assert len(observed) == 2
    assert all(not facts for facts in observed[0].values())
    assert any(() in facts for facts in observed[1].values())


def test_rust_partial_move_uses_shared_field_identity_and_sibling_policy(monkeypatch):
    observed = []
    original = ownership.Place.overlaps

    def overlap(left, right):
        observed.append((left, right))
        return original(left, right)

    monkeypatch.setattr(ownership.Place, "overlaps", overlap)
    source = ("struct P{x:i32} struct Pair{a:P,b:P} "
              "fn f()->i32{let p=Pair{a:P{x:1},b:P{x:2}}; let a=p.a; p.b.x}")
    assert "define" in compile_rust(source, experimental_rustc_checked=True).ir_text
    assert any(left.path == (ownership.Field(0),)
               and right.path == (ownership.Field(1), ownership.Field(0))
               and left.root == right.root for left, right in observed)


def test_rust_copy_and_reinitialization_remain_adapter_policies():
    source = ("struct P{x:i32} fn take(p:P){} fn f()->i32{"
              "let x=7; let y=x; let mut p=P{x:x}; take(p); p=P{x:y}; p.x}")
    assert "define" in compile_rust(source, experimental_rustc_checked=True).ir_text
    with pytest.raises(RustError, match="moved"):
        compile_rust("struct P{x:i32} fn f(){let p=P{x:1}; let q=p; p.x;}",
                     experimental_rustc_checked=True)

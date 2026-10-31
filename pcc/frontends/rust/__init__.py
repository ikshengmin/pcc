"""Experimental Rust scalar frontend; not a safe primary Rust compiler.

No borrow checker is implemented. Callers must explicitly attest that rustc
already checked the unchanged source. PCC never invokes rustc to compile it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .lowering import Lowerer
from .semantics import Checker
from .syntax import Parser, RustError


@dataclass(frozen=True)
class RustCompilation:
    module: object
    symbols: dict[str, str]
    experimental: bool = True
    borrow_checked_by_pcc: bool = False
    overflow_checks: bool = False

    @property
    def ir_text(self) -> str:
        return str(self.module)


def compile_rust(source: str, *, experimental_rustc_checked: bool = False,
                 overflow_checks: bool = False,
                 module_name: str = "rust") -> RustCompilation:
    """Lower a rustc-checked, nonheap scalar crate into PCC's public IR.

    The opt-in is a caller attestation, not a claim that PCC verified a rustc
    receipt. Arithmetic uses Rust's overflow-checks-off wrapping semantics;
    foldable literal/operator overflow is rejected; panic-capable division fails
    closed. This does not claim rustc lint or variable-state constprop parity.
    """
    if experimental_rustc_checked is not True:
        raise RustError("experimental Rust frontend has no borrow checker; only rustc-checked source is accepted with experimental_rustc_checked=True")
    if overflow_checks is not False:
        raise RustError("Rust overflow checks require owned panic support, which is not implemented; only overflow_checks=False is supported")
    functions = Parser(source).parse()
    checker = Checker(functions)
    checker.check()
    lowerer = Lowerer(functions, checker, module_name)
    module = lowerer.lower()
    return RustCompilation(module, dict(lowerer.symbols))


__all__ = ["RustCompilation", "RustError", "compile_rust"]

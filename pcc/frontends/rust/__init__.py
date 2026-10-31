"""Experimental Rust scalar/aggregate frontend; not a safe primary compiler.

No borrow checker is implemented. Callers must explicitly attest that rustc
already checked the unchanged source. PCC never invokes rustc to compile it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .lowering import Lowerer
from .moves import MoveChecker
from .semantics import Checker
from .syntax import Parser, RustError
from .targets import DEFAULT_TARGET, LINUX_LAYOUT, resolve_target


@dataclass(frozen=True)
class RustCompilation:
    module: object
    symbols: dict[str, str]
    experimental: bool = True
    borrow_checked_by_pcc: bool = False
    overflow_checks: bool = False
    target_triple: str = DEFAULT_TARGET
    data_layout: str = LINUX_LAYOUT

    @property
    def ir_text(self) -> str:
        return str(self.module)

    def emit_object(self) -> bytes:
        """Emit a real owned ELF or arm64 Mach-O object, not a header rewrite.

        Cross-emission does not execute or link a program on the target machine.
        Manually retargeting the mutable IR module is rejected: select the target
        at compile_rust's public entry so layout and emitter share one contract.
        """
        target = resolve_target(self.target_triple)
        if (self.module.triple != target.triple
                or self.module.data_layout != target.data_layout
                or self.data_layout != target.data_layout):
            raise RustError("Rust module target/layout changed after compilation; recompile with target_triple")
        from pcc.backend.owned_object_emit import emit_owned_object

        return emit_owned_object(self.ir_text, target.triple)


def compile_rust(source: str, *, experimental_rustc_checked: bool = False,
                 overflow_checks: bool = False,
                 module_name: str = "rust",
                 target_triple: str = DEFAULT_TARGET) -> RustCompilation:
    """Lower a rustc-checked, nonheap scalar/aggregate crate into PCC's public IR.

    The opt-in is a caller attestation, not a claim that PCC verified a rustc
    receipt. Arithmetic uses Rust's overflow-checks-off wrapping semantics;
    foldable literal/operator overflow is rejected; panic-capable division fails
    closed. This does not claim rustc lint or variable-state constprop parity.
    """
    if experimental_rustc_checked is not True:
        raise RustError("experimental Rust frontend has no borrow checker; only rustc-checked source is accepted with experimental_rustc_checked=True")
    if overflow_checks is not False:
        raise RustError("Rust overflow checks require owned panic support, which is not implemented; only overflow_checks=False is supported")
    if type(module_name) is not str:
        raise RustError("invalid Rust module_name: expected a string label")
    target = resolve_target(target_triple)
    parser = Parser(source)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations)
    checker.check()
    MoveChecker(checker).check()
    lowerer = Lowerer(functions, checker, module_name, target)
    module = lowerer.lower()
    return RustCompilation(module, dict(lowerer.symbols),
                           target_triple=target.triple, data_layout=target.data_layout)


__all__ = ["RustCompilation", "RustError", "compile_rust"]

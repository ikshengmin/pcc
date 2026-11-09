"""Compiler-owned Go runtime objects that //go:linkname may bind.

Go runtime/panic.go defines the error values of integer division by zero and
integer overflow, which the runtime also panics with: errorString values whose
Error method returns "runtime error: " followed by the text below. This is the
contract an owned runtime must provide, not a loaded or supplied runtime
package. No storage, initializer or interface value exists yet, so a binding
still reaches the package interface storage boundary.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeErrorValue:
    symbol: str
    text: str

    @property
    def error_text(self):
        return "runtime error: " + self.text


RUNTIME_ERROR_VALUES = {
    value.symbol: value for value in (
        RuntimeErrorValue("runtime.divideError", "integer divide by zero"),
        RuntimeErrorValue("runtime.overflowError", "integer overflow"),
    )
}


def runtime_object(target):
    """The owned runtime object named by a linkname target, or None."""
    return RUNTIME_ERROR_VALUES.get(target)

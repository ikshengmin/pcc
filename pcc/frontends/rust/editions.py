"""Edition identity for API options, not a language-conformance assertion.

These are the crate editions already recognized by Parser/crate_context. An
edition selects existing syntax/policy context; unsupported semantics still
fail at their existing owners. It never grants reference or library admission.
"""
from .syntax import RustError


def require_edition(edition: int) -> int:
    if type(edition) is not int or edition not in {2015, 2018, 2021, 2024}:
        raise RustError('Rust compilation requires an explicit recognized integer edition')
    return edition

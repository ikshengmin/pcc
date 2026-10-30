"""Read ownership and slot identities from exact root-copy call signatures."""

from dataclasses import dataclass
import re

from tests.python.ir_pointer_aliases import (
    canonical_pointer,
    function_bodies,
    pointer_bitcast_aliases,
)


@dataclass(frozen=True)
class RootCopyCall:
    line: str
    destination: str
    source: str
    borrowed: bool
    local: bool


def root_copy_calls(text, *, local=None, borrowed=None):
    """Parse real calls, retaining the distinction between owners and leases."""
    scopes = [body for _name, body in function_bodies(text)] or [text]
    copies = []
    for body in scopes:
        aliases = pointer_bitcast_aliases(body)
        for line in body.splitlines():
            match = re.search(
                r"\bcall\b[^\n]*@pcc_gc_root_copy_(lease|borrowed_lease|local)"
                r"\(([^)]*)\)",
                line,
            )
            if match is None:
                continue
            kind = match[1]
            arguments = [argument.strip() for argument in match[2].split(",")]
            is_local = kind == "local"
            assert len(arguments) == (3 if is_local else 2), line
            slots = [re.fullmatch(r"ptr ([%@][-\w.$]+|null)", argument)
                     for argument in arguments[:2]]
            assert all(slots), line
            if is_local:
                assert arguments[2] in ("i64 0", "i64 1"), line
                is_borrowed = arguments[2] == "i64 1"
            else:
                is_borrowed = kind == "borrowed_lease"
            if local is not None and is_local != local:
                continue
            if borrowed is not None and is_borrowed != borrowed:
                continue
            copies.append(RootCopyCall(
                line,
                canonical_pointer(slots[0][1], aliases),
                canonical_pointer(slots[1][1], aliases),
                is_borrowed,
                is_local,
            ))
    return copies

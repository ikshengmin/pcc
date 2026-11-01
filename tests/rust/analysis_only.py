"""Internal non-certifying semantic models; NEVER produce IR, objects or code.

Legacy move/loan model regressions may inspect these facts. Success is not public
compilation admission and does not authorize a reference-bearing executable.
"""
from pcc.frontends.rust.moves import MoveChecker
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.targets import resolve_target, DEFAULT_TARGET


def analyze_source(source, *, moves=True, target_triple=DEFAULT_TARGET):
    parser = Parser(source)
    functions = parser.parse()
    target = resolve_target(target_triple)
    checker = Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits,
                      externally_checked=False, pointer_bits=target.pointer_bits)
    checker.check()
    if moves:
        MoveChecker(checker).check()
    return checker

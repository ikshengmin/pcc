"""Place-sensitive moves, bounded direct-call shared loans and match-guard borrows.

This is NOT general borrow checking. Borrow patterns, Drop and user-defined Copy are
still rejected. A state records paths which may have moved on any reachable incoming
edge; loop backedges reach a finite fixed point rather than assuming one pass.

A match guard is checked with rustc's borrow rules for guards, owned here
(https://doc.rust-lang.org/reference/expressions/match-expr.html#match-guards):
the arm's bindings are shared references to the matched parts, so they cannot
be moved out of (E0507), assigned (E0594) or mutably borrowed (E0596), and the
places they bind cannot be moved (E0505) or assigned (E0506) during the guard;
the matched place and every place the pattern tests hold shallow borrows that
forbid assigning them or a prefix (E0510). Moving a matched place no binding
covers is accepted by rustc, whose decision tree does not retest it; this
model re-inspects the scrutinee per arm, so that case stays an explicit gap.
"""
from __future__ import annotations

from pcc.analysis.ownership import (
    Field,
    Index,
    LoopEdges,
    Place,
    State,
    clone,
    join,
    prefix,
    solve_loop,
)

from dataclasses import dataclass

from .syntax import Expr


def structural_place(binding, path) -> Place:
    # Rust sema owns resolving names, dereferences and nominal field ordinals.
    # Shared place identity does not independently prove referent non-aliasing.
    return Place(binding.serial, tuple(index if isinstance(index, Index) else Field(index) for index in path))


@dataclass(frozen=True)
class GuardScope:
    """Borrows held while one match guard is evaluated."""
    bindings: dict      # binding serial -> source name of the arm's bindings
    root: object        # the matched place's root binding, or None for a temporary
    bound: tuple        # structural paths the bindings borrow (deeply)
    shallow: tuple      # the matched path and every tested path (shallowly)


def guard_paths(checker, pattern, path, bound, tested):
    """The paths a pattern binds and tests below `path`, as pattern_access projects them."""
    kind = pattern.kind
    if kind == "or":
        for option in pattern.children:
            guard_paths(checker, option, path, bound, tested)
        return
    if kind in {"binding", "at"}:
        bound.append(path)
        if kind == "at":
            guard_paths(checker, pattern.children[0], path, bound, tested)
        return
    if kind in {"wildcard", "unit"}:
        return
    if kind == "literal":
        tested.append(path)
        return
    if kind in {"variant", "payload"}:
        tested.append(path)  # the enum's discriminant
        if kind == "variant":
            return
        path = path + (list(checker.enums[pattern.ty].payloads).index(pattern.variant) + 1,)
    for index, child in enumerate(pattern.children):
        guard_paths(checker, child, path + (index,) if kind in {"tuple", "payload"} else path, bound, tested)


class MoveChecker:
    def __init__(self, checker):
        self.checker = checker
        self.loops: list[LoopEdges] = []
        self.guards: list[GuardScope] = []
        # Ephemeral shared loans exist only while evaluating one direct call.
        # No local reference creation/escape or mutable/interior aliasing is
        # admitted. This is a bounded conflict check, NOT general borrowck.
        self.loans = []

    def conflict(self, binding, path, token):
        place = structural_place(binding, path)
        if any(loan.overlaps(place) for loan in self.loans):
            raise token.error("cannot move or mutate a place while borrowed by a shared call argument")

    def check(self) -> None:
        for function in self.checker.functions:
            self.check_function(function)

    def check_function(self, function) -> None:
        if function.body is None:
            return
        state = {binding.serial: set() for binding in self.checker.parameters[function.identity]}
        self.expression(function.body, state)

    def guard_effect(self, binding, path, token, *, write, mutable_borrow=False):
        """A move (write=False) or write of a place while match guards are evaluated."""
        place = structural_place(binding, path)
        for scope in self.guards:
            name = scope.bindings.get(binding.serial)
            if name is not None:
                if mutable_borrow:
                    raise token.error(f"cannot borrow {name!r} as mutable in a match guard: guard bindings are "
                                      "shared references (E0596)")
                if write:
                    raise token.error(f"cannot assign to {name!r} in a match guard: guard bindings are "
                                      "immutable (E0594)")
                raise token.error(f"cannot move out of {name!r} in a match guard: guard bindings are shared "
                                  "references to the matched value (E0507)")
            if scope.root is None or binding.serial != scope.root:
                continue
            if any(place.overlaps(Place(place.root, item)) for item in scope.bound):
                code, verb = ("E0506", "assign to") if write else ("E0505", "move")
                raise token.error(f"cannot {verb} {binding.name!r} in a match guard while the guard's bindings "
                                  f"borrow it ({code})")
            if write and any(prefix(place.path, item) for item in scope.shallow):
                raise token.error(f"cannot assign {binding.name!r} in a match guard: the match inspects it (E0510)")
            if not write and any(place.overlaps(Place(place.root, item)) for item in scope.shallow):
                raise token.error(f"moving the matched value {binding.name!r} in a match guard requires "
                                  "decision-tree match lowering, which is not implemented")

    def guard_scope(self, pattern, place):
        from .patterns import binding_patterns
        bound, tested = [], []
        if place is not None:
            guard_paths(self.checker, pattern, place[1], bound, tested)
            root = place[0]
            structural = lambda path: structural_place(root, path).path
            return GuardScope({item.binding.serial: item.token.text for item in binding_patterns(pattern)},
                              root.serial, tuple(map(structural, bound)),
                              (structural(place[1]),) + tuple(map(structural, tested)))
        return GuardScope({item.binding.serial: item.token.text for item in binding_patterns(pattern)},
                          None, (), ())

    def access(self, binding, path, ty, token, state, *, consume=False):
        place = structural_place(binding, path)
        moved = state[binding.serial]
        if any(Place(place.root, item).overlaps(place) for item in moved):
            raise token.error(f"use of moved or possibly moved value {binding.name!r}")
        if consume and not self.checker.copy_type(ty):
            if self.guards:
                self.guard_effect(binding, path, token, write=False)
            self.conflict(binding, path, token)
            moved.add(place.path)

    def read_place(self, expr: Expr, state: State, *, consume: bool = True) -> None:
        binding, path = self.checker.place(expr)
        self.access(binding, path, expr.ty, expr.token, state, consume=consume)

    def pattern_access(self, pattern, state, place, *, bind=False):
        # Pattern inspection and selected-arm bindings are separate effects.
        # Inspecting an enum discriminant uses its owning enum place: a prior
        # payload move prevents rematching that enum, even for another field.
        # Tuple wildcards below still permit independent unmoved field access.
        kind = pattern.kind
        if kind == "at":
            # `x @ p` inspects exactly what p inspects. When selected, p's
            # bindings take their parts before x takes the whole value, so a
            # Copy part may be bound beside a moved whole; a non-Copy part
            # cannot be moved twice, even out of a temporary scrutinee.
            from .patterns import binding_patterns
            self.pattern_access(pattern.children[0], state, place, bind=bind)
            if not bind:
                return
            for inner in binding_patterns(pattern.children[0]):
                if not self.checker.copy_type(inner.binding.ty):
                    raise inner.token.error(
                        f"use of moved value: {pattern.token.text!r} @ pattern moves the value "
                        f"that its subpattern binding {inner.token.text!r} also moves")
        if kind in {"binding", "at"}:
            if bind:
                if place is not None:
                    self.access(*place, pattern.ty, pattern.token, state, consume=True)
                state[pattern.binding.serial] = set()
            return
        if kind in {"wildcard", "unit"}:
            return
        if kind == "literal":
            if not bind and place is not None:
                self.access(*place, pattern.ty, pattern.token, state)
            return
        if kind in {"variant", "payload"}:
            if not bind and place is not None:
                self.access(*place, pattern.ty, pattern.token, state)
            if kind == "variant":
                return
            if place is not None:
                index = list(self.checker.enums[pattern.ty].payloads).index(pattern.variant) + 1
                place = place[0], place[1] + (index,)
        for index, child in enumerate(pattern.children):
            child_place = place
            if kind in {"tuple", "payload"} and place is not None:
                child_place = place[0], place[1] + (index,)
            self.pattern_access(child, state, child_place, bind=bind)

    def wildcard_place(self, expr: Expr, state: State) -> State | None:
        """A wildcard omits the final read, not evaluation of its place."""
        if expr.kind in {"group", "field"}:
            return self.wildcard_place(expr.children[0], state)
        if expr.kind == "index":
            # Indexing still reads/borrows its array base, including when the
            # final element is ignored. A moved array cannot supply that place.
            self.read_place(expr.children[0], state, consume=False)
            return self.expression(expr.children[1], state)
        if expr.kind == "deref":
            return self.expression(expr.children[0], state)
        return state

    def expression(self, expr: Expr, state: State) -> State | None:
        kind, children = expr.kind, expr.children
        if kind in {"integer", "bool", "unit", "variant", "const", "static", "string"}:
            return state
        if kind == "borrow":
            if expr.value and self.guards and self.checker.place(children[0]) is not None:
                self.guard_effect(*self.checker.place(children[0]), expr.token, write=True, mutable_borrow=True)
            self.read_place(children[0], state, consume=False)
            # Reborrowing an incoming shared referent does not borrow the local
            # variable which stores that pointer. Rebinding that Copy variable
            # is legal; writing the pointee is separately forbidden by sema.
            if not self.checker.through_reference(children[0]):
                self.loans.append(structural_place(*self.checker.place(children[0])))
            return state
        if kind in {"clone", "derived_eq"}:
            # Clone::clone(&x) and PartialEq::eq(&a, &b) borrow their places;
            # only temporaries are consumed.
            for child in children:
                if state is None:
                    return None
                if self.checker.place(child) is not None:
                    self.read_place(child, state, consume=False)
                else:
                    state = self.expression(child, state)
            return state
        if kind in {"index", "array_len"}:
            target = expr if kind == "index" else children[0]
            if self.checker.place(target) is not None:
                self.read_place(target, state, consume=kind == "index")
            else:
                state = self.expression(children[0], state)
            return self.expression(children[1], state) if kind == "index" and state is not None else state
        if kind == "deref":
            return self.expression(children[0], state)
        if kind in {"name", "field", "group"}:
            if self.checker.place(expr) is not None:
                self.read_place(expr, state)
                return state
            return self.expression(children[0], state)
        if kind == "block":
            visible = set(state)
            current = state
            for child in children:
                if current is not None:
                    current = self.expression(child, current)
            if current is not None:
                for binding in list(current):
                    if binding not in visible:
                        del current[binding]
            return current
        if kind == "wildcard_let":
            # No final place read or move, but place evaluation and value-
            # producing expressions retain their ordinary operand effects.
            if self.checker.place(children[0]) is not None:
                return self.wildcard_place(children[0], state)
            return self.expression(children[0], state)
        if kind == "let":
            result = self.expression(children[0], state)
            if result is not None:
                result[expr.binding.serial] = set()
            return result
        if kind == "pattern_let":
            # The initializer value is moved into the pattern's bindings.
            from .patterns import pattern_bindings
            result = self.expression(children[0], state)
            if result is not None:
                for binding in pattern_bindings(expr.value[0]):
                    result[binding.serial] = set()
            return result
        if kind in {"discard", "statement", "cast", "unary", "unsafe"}:
            return self.expression(children[0], state)
        if kind == "assign" and children[0].kind == "static":
            return self.expression(children[1], state)  # Statics hold Copy data only.
        if kind == "assign":
            result = self.expression(children[1], state)
            if result is None:
                return None
            binding, path = self.checker.place(children[0])
            if self.guards:
                self.guard_effect(binding, path, expr.token, write=True)
            self.conflict(binding, path, expr.token)
            path = structural_place(binding, path).path
            moved = result[binding.serial]
            if expr.value != "=":
                self.read_place(children[0], result, consume=False)
            elif path and any(prefix(item, path) and item != path for item in moved):
                raise expr.token.error("cannot initialize a field beneath a moved aggregate; reinitialize its owning place first")
            # Replacing a whole value or exactly the moved field makes its
            # subplaces available again. Sibling partial moves remain marked.
            result[binding.serial] = {item for item in moved if not prefix(path, item)}
            return result
        if kind == "match":
            from .patterns import pattern_bindings

            scrutinee = children[0]
            place = self.checker.place(scrutinee)
            after = state if place is not None else self.expression(scrutinee, state)
            if after is None:
                return None
            exits = []
            current = after
            for pattern, arm in zip(expr.value, children[1:]):
                self.pattern_access(pattern, current, place)
                selected = current
                if arm.kind == "match_guard":
                    # The guard runs before the scrutinee moves into by-value
                    # bindings (it sees them by reference); a false guard
                    # continues with the next arm carrying the guard's effects.
                    guard, arm = arm.children
                    inspected = clone(current)
                    for binding in pattern_bindings(pattern):
                        inspected[binding.serial] = set()
                    self.guards.append(self.guard_scope(pattern, place))
                    try:
                        selected = self.expression(guard, inspected)
                    finally:
                        self.guards.pop()
                    if selected is None:
                        continue
                    for binding in pattern_bindings(pattern):
                        selected.pop(binding.serial, None)
                    current = join([current, selected])
                branch = clone(selected)
                self.pattern_access(pattern, branch, place, bind=True)
                result = self.expression(arm, branch)
                if result is not None:
                    for binding in pattern_bindings(pattern):
                        result.pop(binding.serial, None)
                    exits.append(result)
            return join(exits)
        if kind == "if":
            after_condition = self.expression(children[0], state)
            if after_condition is None:
                return None
            yes = self.expression(children[1], clone(after_condition))
            no = self.expression(children[2], clone(after_condition)) if len(children) == 3 else clone(after_condition)
            return join([item for item in (yes, no) if item is not None])
        if kind == "binary" and expr.value in {"&&", "||"}:
            after_left = self.expression(children[0], state)
            if after_left is None:
                return None
            after_right = self.expression(children[1], clone(after_left))
            return join([after_left] + ([after_right] if after_right is not None else []))
        if kind == "while_let":
            visible = set(state)
            scrutinee, body_node = children
            place = self.checker.place(scrutinee)

            def transfer(header):
                # Inspect the pattern before consuming selected bindings.
                # Failure exits retain the unbound scrutinee's move state.
                condition = header if place is not None else self.expression(scrutinee, header)
                if condition is None:
                    return [], []
                self.pattern_access(expr.value, condition, place)
                selected = clone(condition)
                self.pattern_access(expr.value, selected, place, bind=True)
                edges = LoopEdges(visible)
                self.loops.append(edges)
                try:
                    body = self.expression(body_node, selected)
                finally:
                    self.loops.pop()
                backs = edges.continues + ([edges.outer(body)] if body is not None else [])
                exits = edges.breaks + [edges.outer(condition)]
                return backs, exits

            return solve_loop(state, transfer)
        if kind in {"for_range", "for_range_inclusive"}:
            # Integer bounds are copied once; the loop then behaves like while.
            current = state
            for child in children[:2]:
                if current is not None:
                    current = self.expression(child, current)
            if current is None:
                return None
            visible = set(current)

            def transfer(header):
                entry = clone(header)
                if expr.binding is not None:
                    entry[expr.binding.serial] = set()
                edges = LoopEdges(visible)
                self.loops.append(edges)
                body = self.expression(children[2], entry)
                self.loops.pop()
                backs = edges.continues + ([edges.outer(body)] if body is not None else [])
                return backs, edges.breaks + [edges.outer(header)]

            return solve_loop(current, transfer)
        if kind in {"while", "loop"}:
            visible = set(state)

            def transfer(header):
                edges = LoopEdges(visible)
                self.loops.append(edges)
                condition = self.expression(children[0], header) if kind == "while" else header
                body_node = children[1] if kind == "while" else children[0]
                body = self.expression(body_node, clone(condition)) if condition is not None else None
                self.loops.pop()
                backs = edges.continues + ([edges.outer(body)] if body is not None else [])
                exits = edges.breaks + ([edges.outer(condition)]
                    if kind == "while" and condition is not None else [])
                return backs, exits

            return solve_loop(state, transfer)
        if kind in {"break", "continue"}:
            edges = self.loops[-1]
            (edges.breaks if kind == "break" else edges.continues).append(edges.outer(state))
            return None
        if kind == "return":
            if children:
                self.expression(children[0], state)
            return None
        # Rust evaluates constructor fields and call arguments in source order,
        # not declaration order. The function-name child is not a local use.
        operands = children[1:] if kind == "call" else children
        active_loans = len(self.loans)
        try:
            current = state
            for child in operands:
                if current is not None:
                    current = self.expression(child, current)
            return current
        finally:
            if kind == "call":
                del self.loans[active_loans:]

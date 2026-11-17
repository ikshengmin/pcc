"""Place-sensitive moves, shared loans with liveness, and match-guard borrows.

This is NOT general borrow checking: mutable loans, regions in signatures, borrow
patterns, Drop and user-defined Copy are still rejected elsewhere. A state records paths which may have moved on any reachable incoming
edge; loop backedges reach a finite fixed point rather than assuming one pass. This
covers loop, while, while let and array for (which moves or copies its array once and
binds a fresh element per iteration); if let is a normalized match.

A match guard is checked with rustc's borrow rules for guards, owned here
(https://doc.rust-lang.org/reference/expressions/match-expr.html#match-guards):
the arm's bindings are shared references to the matched parts, so they cannot
be moved out of (E0507), assigned (E0594) or mutably borrowed (E0596), and the
places they bind cannot be moved (E0505) or assigned (E0506) during the guard;
the matched place and every place the pattern tests hold shallow borrows that
forbid assigning them or a prefix (E0510). Moving a matched place no binding
covers is accepted by rustc, whose decision tree does not retest it; this
model re-inspects the scrutinee per arm, so that case stays an explicit gap.

Shared references follow rustc's non-lexical rules for shared loans
(https://doc.rust-lang.org/reference/types/pointer.html#shared-references-,
https://rust-lang.github.io/rfcs/2094-nll.html). `&place` creates a loan on the
place; a reference-typed local or temporary *holds* the loans of its value, and
copies, reborrows through it and if/match/block values carry them along.
Moving (E0505) or assigning (E0506) an overlapping place, or ending the scope
of the borrowed local (E0597), invalidates the loan in every holder; the error
is reported when an invalidated holder is used afterwards, which is exactly
when the loan was live at the conflict (liveness solved by the same fixed
point as moves; a holder reassigned first never reports). Every mention of a
reference local is a use (`let _ = r;` and `r;` included); a block's reference
value is used when it is stored or consumed, not when it is discarded. A
reference temporary awaiting its consumer (a call argument or operand) is live
by construction, so a conflict with it is immediate. Index projections of one
array always overlap, as in rustc. Reference values produced any other way
(generic calls returning references, references stored in aggregates) are an
explicit gap until region inference exists.
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

from .reference_admission import has_reference
from .syntax import Expr

LOANS = "loans"  # state key (LOANS, serial): the (loan, invalidation) pairs a local holds
WILDCARD_INDEX = Index(None)
# Nodes that forward a child's value instead of consuming it.
FORWARDING = frozenset({"group", "block", "if", "match", "unsafe", "statement", "discard"})
REFERENCE_GAP = ("reference values produced by this expression need region inference (stage 6 of the "
                 "owned admission migration), which is not implemented")


def loan_place(binding, path) -> Place:
    """A loan's place: rustc's borrowck treats any two indices of one array as overlapping."""
    place = structural_place(binding, path)
    return Place(place.root, tuple(WILDCARD_INDEX if isinstance(item, Index) else item for item in place.path))


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
        self.loans = []        # loan places held by reference temporaries awaiting their consumer
        self.loan_info = {}    # loan id -> (loan place, borrow token, borrowed name)
        self.event_info = {}   # invalidation -> (kind, token, name)
        self.produced = {}     # id(expr) -> frozenset of (loan, invalidation) its reference value holds

    @staticmethod
    def declare(state, binding, held=()):
        state[binding.serial] = set()
        state[(LOANS, binding.serial)] = set(held)

    def report(self, event):
        kind, token, name = self.event_info[event]
        if kind == "dead":
            raise token.error(f"{name!r} does not live long enough: it is dropped while still borrowed (E0597)")
        if kind == "move":
            raise token.error(f"cannot move out of {name!r} because it is borrowed (E0505)")
        raise token.error(f"cannot assign to {name!r} because it is borrowed (E0506)")

    def use(self, held):
        """A reference value is used: every loan it holds must still be valid."""
        for _, event in held:
            if event is not None:
                self.report(event)

    def poison(self, state, invalid):
        """Replace each valid holder loan for which invalid(loan) gives an event."""
        for key in [key for key in state if isinstance(key, tuple)]:
            state[key] = {(loan, invalid(loan) if event is None else event) for loan, event in state[key]}

    def invalidate(self, state, binding, path, token, kind):
        """A move or assignment of a place invalidates every overlapping loan."""
        place = loan_place(binding, path)
        if any(loan.overlaps(place) for loan in self.loans):
            self.event_info[(kind, id(token))] = (kind, token, binding.name)
            self.report((kind, id(token)))
        event = (kind, id(token))
        self.event_info[event] = (kind, token, binding.name)
        self.poison(state, lambda loan: event if self.loan_info[loan][0].overlaps(place) else None)

    def end_scope(self, state, dying, values=()):
        """Locals leave scope: loans on them die (E0597 if a holder is used later)."""
        dying = set(dying)
        if not dying:
            return list(values)
        for serial in dying:
            state.pop(serial, None)
            state.pop((LOANS, serial), None)

        def dead(loan):
            place, token, name = self.loan_info[loan]
            if place.root not in dying:
                return None
            self.event_info[("dead", loan)] = ("dead", token, name)
            return ("dead", loan)

        self.poison(state, dead)
        return [frozenset((loan, dead(loan) if event is None else event) for loan, event in value)
                for value in values]

    def leave(self, edges, state):
        """Leave a loop body or iteration: its locals end their scope."""
        self.end_scope(state, [key for key in state if not isinstance(key, tuple) and key not in edges.visible])
        return edges.outer(state)

    def reference_value(self, expr, state):
        """The loans a reference-typed value read from a local holds."""
        if expr.kind == "name" and expr.binding is not None:
            return frozenset(state.get((LOANS, expr.binding.serial), ()))
        if expr.kind in {"group", "unsafe"}:
            child = expr.children[0]
            if id(child) in self.produced:
                return self.produced.pop(id(child))
            return self.reference_value(child, state)
        raise expr.token.error(REFERENCE_GAP)

    def hold_pattern(self, state, pattern, held):
        """Declared pattern bindings take loans; only a whole-value binding can hold references."""
        from .patterns import pattern_bindings
        top = pattern.binding if pattern.kind in {"binding", "at"} else None
        for binding in pattern_bindings(pattern):
            if binding is not top and has_reference(binding.ty):
                raise pattern.token.error(REFERENCE_GAP)
            state[(LOANS, binding.serial)] = set(held if binding is top and has_reference(binding.ty) else ())

    def scrutinee_loans(self, scrutinee, place, state):
        if not has_reference(scrutinee.ty):
            return frozenset()
        if place is None:
            return self.produced.pop(id(scrutinee), frozenset())
        if place[1] or self.checker.through_reference(scrutinee):
            raise scrutinee.token.error(REFERENCE_GAP)
        return frozenset(state.get((LOANS, place[0].serial), ()))

    def check(self) -> None:
        for function in self.checker.functions:
            self.check_function(function)

    def check_function(self, function) -> None:
        if function.body is None:
            return
        state = {}
        for binding in self.checker.parameters[function.identity]:
            self.declare(state, binding)
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
        held = state.get((LOANS, binding.serial))
        if held:
            self.use(held)
        place = structural_place(binding, path)
        moved = state[binding.serial]
        if any(Place(place.root, item).overlaps(place) for item in moved):
            raise token.error(f"use of moved or possibly moved value {binding.name!r}")
        if consume and not self.checker.copy_type(ty):
            if self.guards:
                self.guard_effect(binding, path, token, write=False)
            self.invalidate(state, binding, path, token, "move")
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
                self.declare(state, pattern.binding)
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
        if expr.kind == "name" and expr.binding is not None:
            held = state.get((LOANS, expr.binding.serial))
            if held:
                self.use(held)  # a place mention keeps its loans live
            return state
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
        result = self.evaluate(expr, state)
        if result is None:
            return None
        kind = expr.kind
        if kind not in FORWARDING:
            # A consumer uses its operands' reference values.
            for child in expr.children[1:] if kind == "call" else expr.children:
                held = self.produced.pop(id(child), None)
                if held:
                    self.use(held)
        if id(expr) not in self.produced and has_reference(expr.ty):
            self.produced[id(expr)] = self.reference_value(expr, result)
        return result

    def evaluate(self, expr: Expr, state: State) -> State | None:
        kind, children = expr.kind, expr.children
        if kind in {"integer", "bool", "unit", "variant", "const", "static", "string"}:
            return state
        if kind == "borrow":
            place = self.checker.place(children[0])
            if expr.value and self.guards and place is not None:
                self.guard_effect(*place, expr.token, write=True, mutable_borrow=True)
            self.read_place(children[0], state, consume=False)
            binding, path = place
            if self.checker.through_reference(children[0]):
                # A reborrow through a shared reference borrows its referent,
                # not the local storing the pointer: it holds that local's loans.
                self.produced[id(expr)] = frozenset(state.get((LOANS, binding.serial), ()))
            else:
                self.loan_info[id(expr)] = (loan_place(binding, path), expr.token, binding.name)
                self.produced[id(expr)] = frozenset({(id(expr), None)})
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
                # value is the statement count; one more child is the tail.
                value = self.produced.pop(id(children[-1]), frozenset()) if len(children) > expr.value else frozenset()
                dying = [key for key in current if not isinstance(key, tuple) and key not in visible]
                value, = self.end_scope(current, dying, [value])
                if has_reference(expr.ty):
                    self.produced[id(expr)] = value
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
                held = self.produced.get(id(children[0]), ()) if has_reference(expr.binding.ty) else ()
                self.declare(result, expr.binding, held)
            return result
        if kind == "pattern_let":
            # The initializer value is moved into the pattern's bindings.
            from .patterns import pattern_bindings
            result = self.expression(children[0], state)
            if result is not None:
                for binding in pattern_bindings(expr.value[0]):
                    self.declare(result, binding)
                self.hold_pattern(result, expr.value[0], self.produced.get(id(children[0]), ()))
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
            self.invalidate(result, binding, path, expr.token, "assign")
            if not path and has_reference(binding.ty):
                # Overwriting a reference local replaces the loans it holds.
                result[(LOANS, binding.serial)] = set(self.produced.get(id(children[1]), ()))
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
            held = self.scrutinee_loans(scrutinee, place, after)
            exits, values = [], []
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
                        self.declare(inspected, binding)
                    self.hold_pattern(inspected, pattern, held)
                    self.guards.append(self.guard_scope(pattern, place))
                    try:
                        selected = self.expression(guard, inspected)
                    finally:
                        self.guards.pop()
                    if selected is None:
                        continue
                    for binding in pattern_bindings(pattern):
                        selected.pop(binding.serial, None)
                        selected.pop((LOANS, binding.serial), None)
                    current = join([current, selected])
                branch = clone(selected)
                self.pattern_access(pattern, branch, place, bind=True)
                self.hold_pattern(branch, pattern, held)
                result = self.expression(arm, branch)
                if result is not None:
                    value = self.produced.pop(id(arm), frozenset())
                    value, = self.end_scope(result, [binding.serial for binding in pattern_bindings(pattern)], [value])
                    values.append(value)
                    exits.append(result)
            if has_reference(expr.ty):
                self.produced[id(expr)] = frozenset().union(*values)
            return join(exits)
        if kind == "if":
            after_condition = self.expression(children[0], state)
            if after_condition is None:
                return None
            yes = self.expression(children[1], clone(after_condition))
            no = self.expression(children[2], clone(after_condition)) if len(children) == 3 else clone(after_condition)
            if has_reference(expr.ty):
                self.produced[id(expr)] = frozenset().union(*(
                    self.produced.pop(id(branch), frozenset())
                    for branch, after in zip(children[1:], (yes, no)) if after is not None))
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
                held = self.scrutinee_loans(scrutinee, place, condition)
                self.pattern_access(expr.value, condition, place)
                selected = clone(condition)
                self.pattern_access(expr.value, selected, place, bind=True)
                self.hold_pattern(selected, expr.value, held)
                edges = LoopEdges(visible)
                self.loops.append(edges)
                try:
                    body = self.expression(body_node, selected)
                finally:
                    self.loops.pop()
                backs = edges.continues + ([self.leave(edges, body)] if body is not None else [])
                exits = edges.breaks + [self.leave(edges, condition)]
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
                    self.declare(entry, expr.binding)
                edges = LoopEdges(visible)
                self.loops.append(edges)
                body = self.expression(children[2], entry)
                self.loops.pop()
                backs = edges.continues + ([self.leave(edges, body)] if body is not None else [])
                return backs, edges.breaks + [self.leave(edges, header)]

            return solve_loop(current, transfer)
        if kind == "for":
            # `for x in array` moves (or copies) the array into its IntoIter
            # once; each iteration binds a fresh element, so a body that moves
            # an outer value without reinitializing it fails on the backedge.
            current = self.expression(children[0], state)
            if current is None:
                return None
            visible = set(current)

            def transfer(header):
                entry = clone(header)
                if expr.binding is not None:
                    self.declare(entry, expr.binding)
                edges = LoopEdges(visible)
                self.loops.append(edges)
                try:
                    body = self.expression(children[1], entry)
                finally:
                    self.loops.pop()
                backs = edges.continues + ([self.leave(edges, body)] if body is not None else [])
                return backs, edges.breaks + [self.leave(edges, header)]

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
                backs = edges.continues + ([self.leave(edges, body)] if body is not None else [])
                exits = edges.breaks + ([self.leave(edges, condition)]
                    if kind == "while" and condition is not None else [])
                return backs, exits

            return solve_loop(state, transfer)
        if kind in {"break", "continue"}:
            edges = self.loops[-1]
            (edges.breaks if kind == "break" else edges.continues).append(self.leave(edges, state))
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
                    # The operand's reference value awaits this consumer.
                    self.loans.extend(self.loan_info[loan][0] for loan, _ in self.produced.get(id(child), ()))
            return current
        finally:
            del self.loans[active_loans:]

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

Mutable loans add kinds and explicit dereferences to loan places (stage 5).
An access conflicts with a live loan by rustc's places_conflict: reads and
shared borrows with mutable loans (E0503, E0502), mutable borrows with any
loan (E0499, E0502), writes, moves and scope ends with any loan (E0506,
E0505, E0597), where a write to or the end of a reference does not reach
behind its dereference and a borrow through a shared reference is not
tracked. `&mut` values move on use; sema inserts implicit reborrows at
coercion sites. Autoref receivers and argument reborrows are two-phase:
reserved while their call's operands are evaluated (reads and shared borrows
allowed, mutable loans conflict as E0499) and activated by the call, which
then conflicts with every other live loan, its operands' included.
Reassigning a reference releases the not yet invalidated loans of its old
referent.

Signature regions (stage 6, regions.py): a reference parameter holds an
origin, its universal region, beside loans; values derived from it carry the
origin. A returned value may hold no loan of the function's own storage
(E0515) and only origins that are the output region or 'static ("lifetime may
not live long enough"); assigning into a reference parameter needs the same of
its region. Parameter holders are used at every exit, so a loan of a local
stored in one fails when the local dies (E0597). A call returning a reference
holds the values of the arguments whose region is the callee's output region.
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

from .reference_admission import has_regions
from .syntax import Expr
from .types import SharedReference

LOANS = "loans"  # state key (LOANS, serial): the (loan, invalidation) pairs a local holds
WILDCARD_INDEX = Index(None)
# Nodes that forward a child's value instead of consuming it.
FORWARDING = frozenset({"group", "block", "if", "match", "unsafe", "statement", "discard", "reference_coerce"})


@dataclass(frozen=True)
class Deref:
    """A dereference projection in a loan place (`*r`)."""


@dataclass(frozen=True)
class Inner:
    """An entry of a value's inner region slot: what the data a reference
    refers to holds (`&'s W<'a>`: entries for 'a). Outer entries are bare."""
    loan: object


def unwrap(loan):
    return loan.loan if isinstance(loan, Inner) else loan


def outer_part(held):
    return frozenset(entry for entry in held if not isinstance(entry[0], Inner))


def inner_part(held):
    """The inner slot, unwrapped: the value of the referent."""
    return frozenset((entry[0].loan, entry[1]) for entry in held if isinstance(entry[0], Inner))


def wrap(held):
    return frozenset((Inner(loan), event) for loan, event in held)


DEREF = Deref()
# (access, loan is mutable) -> rustc's error. Reads and shared borrows only
# conflict with mutable loans; writes, moves, mutable borrows and scope ends
# conflict with any loan. Reserving a two-phase borrow conflicts with mutable
# loans only (rustc ignores shared ones until activation).
CONFLICTS = {
    ("read", True): "E0503", ("share", True): "E0502", ("mut", True): "E0499", ("mut", False): "E0502",
    ("reserve", True): "E0499",
    ("write", True): "E0506", ("write", False): "E0506", ("move", True): "E0505", ("move", False): "E0505",
    ("dead", True): "E0597", ("dead", False): "E0597",
}


def conflict_code(loan, mutable, access, kind):
    """rustc's places_conflict for a loan and an access of the given kind."""
    code = CONFLICTS.get((kind, mutable))
    if code is None or loan.root != access.root:
        return None
    if prefix(loan.path, access.path):
        return code  # the access is within the borrowed place
    if prefix(access.path, loan.path):
        # The access encloses the borrowed place. A shallow write (assigning
        # the place) or a scope end does not reach behind a dereference.
        if kind in {"write", "dead"} and DEREF in loan.path[len(access.path):]:
            return None
        return code
    return None
REFERENCE_GAP = ("reference values produced by this expression need region inference (stage 6 of the "
                 "owned admission migration), which is not implemented")


def partial_type(ty):
    from .call_inference import partial
    return partial(ty)


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
    if kind in {"literal", "string_literal"}:
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
        self.loans = []        # (loan place, mutable, two-phase, token, name) of reference temporaries awaiting their consumer
        self.loan_info = {}    # loan id -> (loan place, borrow token, borrowed name, mutable, two-phase)
        self.event_info = {}   # invalidation -> (token, message)
        self.produced = {}     # id(expr) -> frozenset of (loan, invalidation) its reference value holds
        self.parameter_regions = {}  # serial -> region slots of each parameter of the checked function
        self.returns_reference = False
        self.out_regions = ()
        self.anonymous = {}
        self.outlives = set()

    @staticmethod
    def declare(state, binding, held=()):
        state[binding.serial] = set()
        state[(LOANS, binding.serial)] = set(held)

    @staticmethod
    def message(code, kind, name):
        if code == "E0597":
            return f"{name!r} does not live long enough: it is dropped while still borrowed (E0597)"
        if code == "E0505":
            return f"cannot move out of {name!r} because it is borrowed (E0505)"
        if code == "E0506":
            return f"cannot assign to {name!r} because it is borrowed (E0506)"
        if code == "E0503":
            return f"cannot use {name!r} because it was mutably borrowed (E0503)"
        if code == "E0499":
            return f"cannot borrow {name!r} as mutable more than once at a time (E0499)"
        if kind == "share":
            return f"cannot borrow {name!r} as immutable because it is also borrowed as mutable (E0502)"
        return f"cannot borrow {name!r} as mutable because it is also borrowed as immutable (E0502)"

    def report(self, event):
        token, message = self.event_info[event]
        raise token.error(message)

    def use(self, held):
        """A reference value is used: every loan it holds must still be valid."""
        for _, event in held:
            if event is not None:
                self.report(event)

    def poison(self, state, invalid):
        """Replace each valid holder loan for which invalid(loan) gives an event
        (origins, the non-integer entries, are never invalidated)."""
        for key in [key for key in state if isinstance(key, tuple)]:
            state[key] = {(loan, invalid(unwrap(loan)) if event is None and type(unwrap(loan)) is int else event)
                          for loan, event in state[key]}

    def invalidate(self, state, target, token, kind):
        """An access (read, share, mut, write, move) of a place invalidates the
        loans it conflicts with in every holder; a conflict with a reference
        temporary awaiting its consumer is immediate. A reserved two-phase
        borrow admits reads and shared borrows until its call activates it."""
        binding, path, _ = target
        place = Place(binding.serial, path)
        for loan, mutable, two_phase, _, _ in self.loans:
            if two_phase and kind in {"read", "share"}:
                continue
            code = conflict_code(loan, mutable, place, kind)
            if code is not None:
                raise token.error(self.message(code, kind, binding.name))

        def event(loan):
            loan_place_, _, _, mutable, _ = self.loan_info[loan]
            code = conflict_code(loan_place_, mutable, place, kind)
            if code is None:
                return None
            key = (code, kind, id(token))
            self.event_info[key] = (token, self.message(code, kind, binding.name))
            return key

        self.poison(state, event)

    def activate(self, state, start, token):
        """A call activates its reserved two-phase borrows: a mutable access
        that conflicts with every other loan still live, its other operands
        included (rustc's two-phase borrow activation)."""
        pending = self.loans[start:]
        for index, (place, _, two_phase, borrow, name) in enumerate(pending):
            if not two_phase:
                continue
            for other, (loan, other_mutable, _, _, _) in enumerate(pending):
                code = conflict_code(loan, other_mutable, place, "mut") if other != index else None
                if code is not None:
                    raise borrow.error(self.message(code, "mut", name))

            def event(loan, place=place, borrow=borrow, name=name):
                loan_place_, _, _, loan_mutable, _ = self.loan_info[loan]
                code = conflict_code(loan_place_, loan_mutable, place, "mut")
                if code is None:
                    return None
                key = (code, "mut", id(borrow))
                self.event_info[key] = (borrow, self.message(code, "mut", name))
                return key

            self.poison(state, event)

    def end_scope(self, state, dying, values=()):
        """Locals leave scope: loans on them die (E0597 if a holder is used later)."""
        dying = set(dying)
        if not dying:
            return list(values)
        for serial in dying:
            state.pop(serial, None)
            state.pop((LOANS, serial), None)

        def dead(loan):
            place, token, name, mutable, _ = self.loan_info[loan]
            if place.root not in dying or conflict_code(place, mutable, Place(place.root), "dead") is None:
                return None  # what lies behind a dying reference outlives it
            self.event_info[("E0597", loan)] = (token, self.message("E0597", "dead", name))
            return ("E0597", loan)

        self.poison(state, dead)
        return [frozenset((loan, dead(unwrap(loan)) if event is None and type(unwrap(loan)) is int else event)
                          for loan, event in value)
                for value in values]

    def leave(self, edges, state):
        """Leave a loop body or iteration: its locals end their scope."""
        self.end_scope(state, [key for key in state if not isinstance(key, tuple) and key not in edges.visible])
        return edges.outer(state)

    def reference_value(self, expr, state):
        """What a value with regions holds: read from a place, forwarded or built."""
        kind = expr.kind
        if kind in {"group", "unsafe", "reference_coerce"}:
            child = expr.children[0]
            if kind == "reference_coerce":
                from .types import STATIC_STR, is_borrowed_str
                if child.ty == STATIC_STR and is_borrowed_str(expr.ty):
                    return frozenset()  # Only permanently live literal-backed data is shortened.
            if id(child) in self.produced:
                return self.produced.pop(id(child))
            return self.reference_value(child, state)
        value = self.place_value(expr, state)
        if value is not None:
            return value
        if kind in {"construct", "enum_construct", "variant"}:
            # A struct or enum value holds what its fields with regions hold.
            return frozenset().union(*(outer_part(self.produced.get(id(child), frozenset()))
                                       for child in expr.children))
        if kind == "clone":
            return self.place_value(expr.children[0], state) or frozenset()
        raise expr.token.error(REFERENCE_GAP)

    def place_value(self, expr, state):
        """What the value at a place (or reached from a temporary) holds, or
        None when expr is neither: a reference's referent is its inner slot,
        and a field with regions shares its data type's single slot."""
        kind = expr.kind
        if kind == "group":
            return self.place_value(expr.children[0], state)
        if kind == "name":
            return frozenset(state.get((LOANS, expr.binding.serial), ())) if expr.binding is not None else None
        if kind in {"deref", "field"}:
            base = expr.children[0]
            held = self.place_value(base, state)
            if held is None:
                held = self.produced.get(id(base))
            if held is None:
                return None
            if kind == "deref" or isinstance(partial_type(base.ty), SharedReference):
                held = inner_part(held)
            return held if self.slots(expr.ty) else frozenset()
        return None

    def last_reference(self, expr):
        """The reference expression a place is last dereferenced through, or None."""
        kind = expr.kind
        if kind == "group":
            return self.last_reference(expr.children[0])
        if kind == "deref":
            return expr.children[0]
        if kind in {"field", "index"}:
            base = expr.children[0]
            return base if isinstance(partial_type(base.ty), SharedReference) else self.last_reference(base)
        return None

    def slots(self, ty):
        from .regions import slot_count
        count = slot_count(ty, self.checker)
        return count if count is not None else 0

    def regions(self, ty):
        return has_regions(ty, self.checker)

    def call_result(self, expr, operands):
        """Each region slot of a call's result holds the argument slots that
        have the callee's region for it (none for 'static)."""
        from .regions import STATIC, signature_regions
        regions, out = signature_regions(expr.binding, self.checker)
        held = frozenset()
        for index, region in enumerate(out):
            if region == STATIC:
                continue
            for operand, slots in zip(operands, regions):
                value = self.produced.get(id(operand), frozenset())
                for slot, argument_region in enumerate(slots):
                    if argument_region == region:
                        part = outer_part(value) if slot == 0 else inner_part(value)
                        held |= part if index == 0 else wrap(part)
        return held

    def loan_target(self, expr):
        """(binding, loan path, untracked) of a place expression with explicit
        dereferences; untracked when the path passes through a shared
        reference, whose referent no loan can conflict with."""
        from .call_inference import partial
        from .types import SharedReference
        kind = expr.kind
        if kind == "group":
            return self.loan_target(expr.children[0])
        if kind == "name":
            return (expr.binding, (), False) if expr.binding is not None else None
        if kind in {"deref", "field", "index"}:
            child = expr.children[0]
            base = self.loan_target(child)
            if base is None:
                return None
            binding, path, untracked = base
            pointer = partial(child.ty)
            if isinstance(pointer, SharedReference):
                path, untracked = path + (DEREF,), untracked or not pointer.mutable
            if kind == "field":
                path += (Field(expr.value),)
            elif kind == "index":
                path += (WILDCARD_INDEX,)
            return binding, path, untracked
        return None

    def hold_pattern(self, state, pattern, held):
        """Declared pattern bindings take what the scrutinee value holds: the
        whole value, or the single slot its parts with regions share."""
        from .patterns import pattern_bindings
        top = pattern.binding if pattern.kind in {"binding", "at"} else None
        for binding in pattern_bindings(pattern):
            slots = self.slots(binding.ty)
            if binding is not top and slots > 1:
                raise pattern.token.error(REFERENCE_GAP)
            state[(LOANS, binding.serial)] = set((held if binding is top else outer_part(held)) if slots else ())

    def scrutinee_loans(self, scrutinee, place, state):
        if not self.regions(scrutinee.ty):
            return frozenset()
        if place is None:
            return self.produced.pop(id(scrutinee), frozenset())
        value = self.place_value(scrutinee, state)
        if value is None:
            raise scrutinee.token.error(REFERENCE_GAP)
        return value

    def check(self) -> None:
        for function in self.checker.functions:
            self.check_function(function)

    def check_function(self, function) -> None:
        if function.body is None:
            return
        from .regions import signature_regions
        regions, self.out_regions = signature_regions(function, self.checker)
        self.returns_reference = self.regions(function.result)
        state = {}
        parameters = self.checker.parameters[function.identity]
        self.parameter_regions = {}
        self.anonymous = {}  # anonymous region -> the parameter that has it (E0621)
        # Implied bounds: a well-formed `&'s W<'a>` parameter means 'a: 's.
        self.outlives = {(slots[1], slots[0]) for slots in regions if len(slots) == 2}
        for binding, slots in zip(parameters, regions):
            held = set()
            for slot, region in enumerate(slots):
                held |= ({(("origin", region), None)} if slot == 0 else wrap({(("origin", region), None)}))
                self.anonymous.setdefault(region, binding.name)
            self.declare(state, binding, held)
            self.parameter_regions[binding.serial] = slots
        result = self.expression(function.body, state)
        if result is not None:
            if self.returns_reference:
                self.returned(self.produced.pop(id(function.body), frozenset()), function.body.token)
            self.exit(result)

    def returned(self, value, token):
        """A returned value may hold no loan of this function's own storage
        (E0515) and, in each region slot, only that slot's output region or
        'static as origins."""
        for slot, part in enumerate((outer_part(value), inner_part(value))):
            region = self.out_regions[slot] if slot < len(self.out_regions) else None
            for loan, _ in part:
                if type(loan) is int:
                    place, _, name, _, _ = self.loan_info[loan]
                    if DEREF not in place.path:
                        raise token.error(f"cannot return a reference to {name!r}: it is data owned by the "
                                          "current function (E0515)")
                else:
                    self.require_region(loan[1], region, token)
        self.use(value)

    def require_slots(self, value, slots, token):
        """Data stored where a parameter's regions are required."""
        for slot, part in enumerate((outer_part(value), inner_part(value))):
            for loan, _ in part:
                if type(loan) is not int:
                    self.require_region(loan[1], slots[slot] if slot < len(slots) else None, token)

    def write_into(self, state, reference, written, token, seen=None):
        """The data a reference value refers to is written with `written` (a
        one-slot value): every local it may point to gains those loans; data
        the caller owns (an origin) needs them to outlive its region."""
        seen = set() if seen is None else seen
        written = outer_part(written)
        for loan, _ in outer_part(reference):
            if type(loan) is not int:
                # Caller memory: its data has the reference's inner region.
                for inner, _ in inner_part(reference):
                    region = inner[1] if type(inner) is not int else None
                    for item, _ in written:
                        if type(item) is int:
                            place, borrow, name, _, _ = self.loan_info[item]
                            if DEREF not in place.path:
                                raise borrow.error(f"{name!r} does not live long enough: it is stored into data "
                                                   "the caller owns (E0597)")
                        elif region is not None:
                            self.require_region(item[1], region, token)
                continue
            place = self.loan_info[loan][0]
            if place.root in seen:
                continue
            seen.add(place.root)
            if DEREF in place.path:
                # Behind another reference: follow what that reference holds.
                self.write_into(state, frozenset(state.get((LOANS, place.root), ())), written, token, seen)
            elif (LOANS, place.root) in state:
                state[(LOANS, place.root)] |= written

    def call_writes(self, state, expr, operands):
        """A `&mut` argument to data with a region may be written by the
        callee with the arguments that share that region."""
        from .regions import signature_regions
        regions, _ = signature_regions(expr.binding, self.checker)
        for (_, ty, _), operand, slots in zip(expr.binding.params, operands, regions):
            ty = partial_type(ty)
            if not (isinstance(ty, SharedReference) and ty.mutable and len(slots) == 2):
                continue
            written = frozenset()
            for other, other_slots in zip(operands, regions):
                if other is operand:
                    continue
                value = self.produced.get(id(other), frozenset())
                for slot, region in enumerate(other_slots):
                    if region == slots[1]:
                        written |= outer_part(value) if slot == 0 else inner_part(value)
            if written:
                self.write_into(state, self.produced.get(id(operand), frozenset()), written, expr.token)

    def require_region(self, origin, region, token):
        """Data of a parameter's region may flow only where that region (or
        'static data) is required."""
        from .regions import STATIC
        if origin in (region, STATIC) or (origin, region) in self.outlives:
            return
        if origin.startswith("'_") and region is not None and not region.startswith("'_") and region != STATIC:
            raise token.error(f"explicit lifetime required in the type of {self.anonymous.get(origin, 'a parameter')!r}"
                              f": lifetime {region} required (E0621)")
        raise token.error("lifetime may not live long enough: the value comes from a parameter whose lifetime "
                          "is not the one required")

    def exit(self, state):
        """At a function exit every local dies while the parameters' regions
        are still live: a parameter holding a loan of a local fails (E0597)."""
        state = clone(state)
        self.end_scope(state, [key for key in state if not isinstance(key, tuple)
                               and key not in self.parameter_regions])
        for serial in self.parameter_regions:
            held = state.get((LOANS, serial))
            if held:
                self.use(held)

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

    def access(self, binding, path, ty, token, state, *, consume=False, target=None, effect="read"):
        """Use a place: check moves and holder loans, then move it (non-Copy
        consume) or apply `effect` (a read, or None) to conflicting loans.
        `target` is its loan place with explicit dereferences."""
        held = state.get((LOANS, binding.serial))
        if held:
            self.use(held)
        place = structural_place(binding, path)
        moved = state[binding.serial]
        if any(Place(place.root, item).overlaps(place) for item in moved):
            raise token.error(f"use of moved or possibly moved value {binding.name!r}")
        if target is None:
            target = (binding, loan_place(binding, path).path, False)
        if consume and not self.checker.copy_type(ty):
            if self.guards:
                self.guard_effect(binding, path, token, write=False)
            self.invalidate(state, target, token, "move")
            moved.add(place.path)
        elif effect is not None:
            self.invalidate(state, target, token, effect)

    def read_place(self, expr: Expr, state: State, *, consume: bool = True, effect="read") -> None:
        binding, path = self.checker.place(expr)
        self.access(binding, path, expr.ty, expr.token, state, consume=consume,
                    target=self.loan_target(expr), effect=effect)

    def pattern_access(self, pattern, state, place, *, bind=False, target=None):
        # Pattern inspection and selected-arm bindings are separate effects.
        # `target` projects the scrutinee's loan place (explicit dereferences)
        # alongside `place`.
        if place is not None and target is None:
            target = (place[0], loan_place(*place).path, False)
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
            self.pattern_access(pattern.children[0], state, place, bind=bind, target=target)
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
                    self.access(*place, pattern.ty, pattern.token, state, consume=True, target=target)
                self.declare(state, pattern.binding)
            return
        if kind in {"wildcard", "unit"}:
            return
        if kind in {"literal", "string_literal"}:
            if not bind and place is not None:
                self.access(*place, pattern.ty, pattern.token, state, target=target)
            return
        if kind in {"variant", "payload"}:
            if not bind and place is not None:
                self.access(*place, pattern.ty, pattern.token, state, target=target)
            if kind == "variant":
                return
            if place is not None:
                index = list(self.checker.enums[pattern.ty].payloads).index(pattern.variant) + 1
                place = place[0], place[1] + (index,)
                target = target[0], target[1] + (Field(index),), target[2]
        for index, child in enumerate(pattern.children):
            child_place, child_target = place, target
            if kind in {"tuple", "payload"} and place is not None:
                child_place = place[0], place[1] + (index,)
                child_target = target[0], target[1] + (Field(index),), target[2]
            self.pattern_access(child, state, child_place, bind=bind, target=child_target)

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
        # The value first (a field or constructor reads its operands), then
        # the consumer uses its operands' values.
        if id(expr) not in self.produced and self.regions(expr.ty):
            self.produced[id(expr)] = self.reference_value(expr, result)
        if kind not in FORWARDING:
            for child in expr.children[1:] if kind == "call" else expr.children:
                held = self.produced.pop(id(child), None)
                if held:
                    self.use(held)
        return result

    def evaluate(self, expr: Expr, state: State) -> State | None:
        kind, children = expr.kind, expr.children
        if kind in {"integer", "bool", "character", "unit", "variant", "const", "static", "string"}:
            return state
        if kind == "borrow":
            from .semantics import TWO_PHASE
            place = self.checker.place(children[0])
            if expr.value and self.guards and place is not None:
                self.guard_effect(*place, expr.token, write=True, mutable_borrow=True)
            self.read_place(children[0], state, consume=False, effect=None)
            binding, path, untracked = target = self.loan_target(children[0])
            # A reborrow holds what the last dereferenced reference holds
            # (rustc's reborrow constraints): both its slots for a reference
            # to data with a region, else one (implied bounds keep both).
            through = self.last_reference(children[0])
            via = frozenset()
            if through is not None:
                via = self.place_value(through, state)
                via = self.produced.get(id(through), frozenset()) if via is None else via
            if self.slots(expr.ty) != 2:
                # Region-free data reached through `&'s W<'a>`: only 's.
                via = outer_part(via)
            if untracked:
                # A borrow through a shared reference is not tracked.
                self.produced[id(expr)] = via
                return state
            mutable, two_phase = bool(expr.value), expr.value == TWO_PHASE
            # A two-phase borrow is reserved here and activated by its call.
            self.invalidate(state, target, expr.token,
                            "reserve" if two_phase else "mut" if mutable else "share")
            self.loan_info[id(expr)] = (Place(binding.serial, path), expr.token, binding.name, mutable, two_phase)
            value = frozenset({(id(expr), None)})
            if DEREF in path:
                value |= via  # a reborrow through `&mut` keeps the reference's own loans live
            elif self.slots(expr.ty) == 2:
                value |= wrap(self.place_value(children[0], state) or frozenset())  # `&w` of a `W<'a>`
            self.produced[id(expr)] = value
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
        if kind in {"index", "array_len", "str_length"}:
            target = expr if kind == "index" else children[0]
            if self.checker.place(target) is not None:
                self.read_place(target, state, consume=kind == "index")
            else:
                state = self.expression(children[0], state)
            return self.expression(children[1], state) if kind == "index" and state is not None else state
        if kind == "deref":
            if self.checker.place(children[0]) is not None:
                self.read_place(children[0], state, consume=False)  # reading through r never moves r
                return state
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
                if self.regions(expr.ty):
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
                held = self.produced.get(id(children[0]), ()) if self.regions(expr.binding.ty) else ()
                self.declare(result, expr.binding, held)
            return result
        if kind == "pattern_let":
            from .patterns import pattern_bindings
            init, pattern = children[0], expr.value[0]
            place = self.checker.place(init)
            if place is not None:
                # Matched in place like a match scrutinee: by-value bindings
                # take (move or copy) their parts, the rest stays usable.
                target = self.loan_target(init)
                held = self.scrutinee_loans(init, place, state)
                self.pattern_access(pattern, state, place, target=target)
                self.pattern_access(pattern, state, place, bind=True, target=target)
                self.hold_pattern(state, pattern, held)
                return state
            # A value initializer is moved into the pattern's bindings.
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
            lhs = children[0]
            if self.checker.place(lhs) is None:
                # Writing through a reference temporary (`*s.slot() = v`): the
                # temporary is evaluated and used; its own loans already keep
                # every other access to the referent out.
                base = self.checker.reference_temporary(lhs)
                result = self.expression(base, result)
                held = self.produced.pop(id(base), None) if result is not None else None
                if held:
                    self.use(held)
                return result
            binding, path = self.checker.place(lhs)
            target = self.loan_target(lhs)
            if self.guards:
                self.guard_effect(binding, path, expr.token, write=True)
            if expr.value != "=":
                # A compound assignment reads first (E0503 against a mutable loan).
                self.read_place(lhs, result, consume=False)
            elif DEREF in target[1]:
                # Writing through a reference uses the reference.
                self.access(binding, (), binding.ty, lhs.token, result, effect=None)
            self.invalidate(result, target, expr.token, "write")
            value = self.produced.get(id(children[1]), frozenset())
            if DEREF in target[1]:
                if self.regions(lhs.ty):
                    # The referent gains what the written value holds.
                    through = self.last_reference(lhs)
                    self.write_into(result, self.place_value(through, result) or frozenset(), value, expr.token)
                    if through.kind == "name" and through.binding is not None:
                        result[(LOANS, through.binding.serial)] |= wrap(outer_part(value))
                return result  # the referent is written; the local is not reinitialized
            slots = self.parameter_regions.get(binding.serial)
            if path and self.regions(lhs.ty):
                # A field with regions shares its data type's single slot.
                result[(LOANS, binding.serial)] |= outer_part(value)
                if slots:
                    self.require_slots(outer_part(value), slots[:1], expr.token)
            if not path and self.regions(binding.ty):
                # Overwriting a local with regions replaces what it holds, and
                # loans of its old referent (`*r`) no longer concern the local
                # (rustc kills them); an already invalidated one stays.
                result[(LOANS, binding.serial)] = set(value)
                if slots:
                    self.require_slots(value, slots, expr.token)
                for key in [key for key in result if isinstance(key, tuple)]:
                    result[key] = {(loan, event) for loan, event in result[key]
                                   if event is not None or type(unwrap(loan)) is not int
                                   or self.loan_info[unwrap(loan)][0].root != binding.serial
                                   or DEREF not in self.loan_info[unwrap(loan)][0].path}
            path = structural_place(binding, path).path
            moved = result[binding.serial]
            if expr.value == "=" and path and any(prefix(item, path) and item != path for item in moved):
                raise expr.token.error("cannot initialize a field beneath a moved aggregate; reinitialize its owning place first")
            # Replacing a whole value or exactly the moved field makes its
            # subplaces available again. Sibling partial moves remain marked.
            result[binding.serial] = {item for item in moved if not prefix(path, item)}
            return result
        if kind == "match":
            from .patterns import pattern_bindings

            scrutinee = children[0]
            place = self.checker.place(scrutinee)
            # Wildcards do not test or bind any part of a string. For a
            # simple local (no place projections or dereferences), if EVERY
            # arm is exactly a wildcard, a guard may assign that local: no
            # later arm inspects its captured descriptor. Other match shapes
            # keep their existing shallow loans, including string literals.
            from .types import STATIC_STR, is_borrowed_str
            uninspected_string = (scrutinee.kind == 'name' and place is not None and not place[1]
                                  and (scrutinee.ty == STATIC_STR or is_borrowed_str(scrutinee.ty))
                                  and all(p.kind == 'wildcard' for p in expr.value))
            target = self.loan_target(scrutinee) if place is not None else None
            after = state if place is not None else self.expression(scrutinee, state)
            if after is None:
                return None
            held = self.scrutinee_loans(scrutinee, place, after)
            exits, values = [], []
            current = after
            for pattern, arm in zip(expr.value, children[1:]):
                self.pattern_access(pattern, current, place, target=target)
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
                    self.guards.append(self.guard_scope(pattern, None if uninspected_string else place))
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
                self.pattern_access(pattern, branch, place, bind=True, target=target)
                self.hold_pattern(branch, pattern, held)
                result = self.expression(arm, branch)
                if result is not None:
                    value = self.produced.pop(id(arm), frozenset())
                    value, = self.end_scope(result, [binding.serial for binding in pattern_bindings(pattern)], [value])
                    values.append(value)
                    exits.append(result)
            if self.regions(expr.ty):
                self.produced[id(expr)] = frozenset().union(*values)
            return join(exits)
        if kind == "if":
            after_condition = self.expression(children[0], state)
            if after_condition is None:
                return None
            yes = self.expression(children[1], clone(after_condition))
            no = self.expression(children[2], clone(after_condition)) if len(children) == 3 else clone(after_condition)
            if self.regions(expr.ty):
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
            target = self.loan_target(scrutinee) if place is not None else None

            def transfer(header):
                # Inspect the pattern before consuming selected bindings.
                # Failure exits retain the unbound scrutinee's move state.
                condition = header if place is not None else self.expression(scrutinee, header)
                if condition is None:
                    return [], []
                held = self.scrutinee_loans(scrutinee, place, condition)
                self.pattern_access(expr.value, condition, place, target=target)
                selected = clone(condition)
                self.pattern_access(expr.value, selected, place, bind=True, target=target)
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
                state = self.expression(children[0], state)
                if state is None:
                    return None
                if self.returns_reference:
                    self.returned(self.produced.pop(id(children[0]), frozenset()), children[0].token)
            self.exit(state)
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
                    for loan, _ in self.produced.get(id(child), ()):
                        loan = unwrap(loan)
                        if type(loan) is not int:
                            continue  # an origin, not a loan
                        place, token, name, mutable, two_phase = self.loan_info[loan]
                        # Only the operand that is the two-phase borrow itself
                        # is reserved; one a call result holds is active.
                        self.loans.append((place, mutable, two_phase and loan == id(child), token, name))
            if kind == "call" and current is not None:
                self.activate(current, active_loans, expr.token)
                self.call_writes(current, expr, operands)
                if self.regions(expr.ty):
                    self.produced[id(expr)] = self.call_result(expr, operands)
            return current
        finally:
            del self.loans[active_loans:]

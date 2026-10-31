"""Place-sensitive moves for the borrow-free aggregate slice.

This is NOT borrow checking. Borrow patterns, Drop and user-defined Copy are
still rejected. A state records paths which may have moved on any reachable incoming
edge; loop backedges reach a finite fixed point rather than assuming one pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .syntax import Expr

State = dict[int, set[tuple[int, ...]]]


def clone(state: State) -> State:
    return {binding: set(paths) for binding, paths in state.items()}


def join(states: list[State]) -> State | None:
    if not states:
        return None
    result = clone(states[0])
    for state in states[1:]:
        for binding, paths in state.items():
            result.setdefault(binding, set()).update(paths)
    return result


def prefix(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    return right[:len(left)] == left


@dataclass
class LoopEdges:
    visible: set[int]
    breaks: list[State] = field(default_factory=list)
    continues: list[State] = field(default_factory=list)

    def outer(self, state: State) -> State:
        return {binding: set(paths) for binding, paths in state.items() if binding in self.visible}


class MoveChecker:
    def __init__(self, checker):
        self.checker = checker
        self.loops: list[LoopEdges] = []

    def check(self) -> None:
        for function in self.checker.functions:
            if function.body is None:
                continue
            state = {binding.serial: set() for binding in self.checker.parameters[function.token.text]}
            self.expression(function.body, state)

    def access(self, binding, path, ty, token, state, *, consume=False, shallow=False):
        moved = state[binding.serial]
        if any(prefix(item, path) or (not shallow and prefix(path, item)) for item in moved):
            raise token.error(f"use of moved or possibly moved value {binding.name!r}")
        if consume and not self.checker.copy_type(ty):
            moved.add(path)

    def read_place(self, expr: Expr, state: State, *, consume: bool = True) -> None:
        binding, path = self.checker.place(expr)
        self.access(binding, path, expr.ty, expr.token, state, consume=consume)

    def pattern_access(self, pattern, state, place, *, bind=False):
        # Pattern inspection and selected-arm bindings are separate effects.
        # Discriminant reads need the enum itself, not its moved subfields.
        kind = pattern.kind
        if kind == "binding":
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
                self.access(*place, pattern.ty, pattern.token, state, shallow=True)
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

    def expression(self, expr: Expr, state: State) -> State | None:
        kind, children = expr.kind, expr.children
        if kind in {"integer", "bool", "unit", "variant"}:
            return state
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
        if kind == "let":
            result = self.expression(children[0], state)
            if result is not None:
                result[expr.binding.serial] = set()
            return result
        if kind in {"discard", "statement", "cast", "unary", "unsafe"}:
            return self.expression(children[0], state)
        if kind == "assign":
            result = self.expression(children[1], state)
            if result is None:
                return None
            binding, path = self.checker.place(children[0])
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
            for pattern, arm in zip(expr.value, children[1:]):
                self.pattern_access(pattern, after, place)
                branch = clone(after)
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
        if kind == "while":
            entry = clone(state)
            header = clone(state)
            while True:
                edges = LoopEdges(set(entry))
                self.loops.append(edges)
                condition = self.expression(children[0], clone(header))
                body = self.expression(children[1], clone(condition)) if condition is not None else None
                self.loops.pop()
                backs = edges.continues + ([edges.outer(body)] if body is not None else [])
                next_header = join([entry] + backs)
                if next_header == header:
                    exits = edges.breaks + ([edges.outer(condition)] if condition is not None else [])
                    return join(exits)
                header = next_header
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
        current = state
        for child in operands:
            if current is not None:
                current = self.expression(child, current)
        return current

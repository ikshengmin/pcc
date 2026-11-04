"""JLS 4.12.3/16 definite unassignment for native primitive blank-final locals.

This pass consumes an already typed, bound AST. It never runs the binder or
changes DA/reachability facts, expression constants, storage or native lowering.
DU is an independent must property, not the complement of definite assignment.
The finite bitset graph starts at top and reaches its greatest fixed point.
That implements the hypothetical loop/backedge rules in JLS 16.2.10-12.
Vacuous boolean outcomes and abrupt normal completions are top, not erased
paths: even dead syntax may remove DU under the structural Java rules.
"""
from collections import deque
from dataclasses import dataclass, field


@dataclass
class FlowPoint:
    predecessors: list = field(default_factory=list)
    successors: list = field(default_factory=list)
    generate: int = 0
    kill: int = 0


class BlankFinalFlow:
    def __init__(self, method):
        self.bits = {binding: 1 << index for index, binding in enumerate(
            binding for binding in method.locals if binding.is_blank_final)}
        self.universe = (1 << len(self.bits)) - 1
        self.points = []
        self.writes = []
        self.targets = {}

    def point(self, *predecessors, generate=0, kill=0):
        index = len(self.points)
        self.points.append(FlowPoint(generate=generate, kill=kill))
        for predecessor in dict.fromkeys(predecessors):
            self.edge(predecessor, index)
        return index

    def edge(self, source, destination):
        if source is not None:
            self.points[source].successors.append(destination)
            self.points[destination].predecessors.append(source)

    def write(self, node, incoming):
        bit = self.bits.get(node.symbol, 0)
        if not bit:
            return incoming
        point = self.point(incoming, kill=bit)
        self.writes.append((point, node, bit))
        return point

    def scope_entry(self, statements, incoming):
        # JLS 4.12.3: direct locals are created on block entry, even when
        # switch dispatch bypasses their declaration. A declaration sequence
        # shares this scope; nested blocks/for headers own different entries.
        generate = 0
        remaining = list(statements)
        while remaining:
            node = remaining.pop()
            if node.kind == "var":
                generate |= self.bits.get(node.symbol, 0)
            elif node.kind == "sequence":
                remaining.extend(node.children)
        return self.point(incoming, generate=generate) if generate else incoming

    def expression(self, node, incoming):
        if node.dtype == "boolean":
            return self.point(*self.condition(node, incoming))
        return self.value(node, incoming)

    def condition(self, node, incoming):
        if node.constant is True:
            return incoming, self.point(generate=self.universe)
        if node.constant is False:
            return self.point(generate=self.universe), incoming
        kind = node.kind
        if kind == "group":
            return self.condition(node.children[0], incoming)
        if kind == "unary" and node.value == "!":
            yes, no = self.condition(node.children[0], incoming)
            return no, yes
        if kind == "binary" and node.value in {"&&", "||"}:
            left_true, left_false = self.condition(node.children[0], incoming)
            right_true, right_false = self.condition(
                node.children[1], left_true if node.value == "&&" else left_false)
            return ((right_true, self.point(left_false, right_false)) if node.value == "&&" else
                    (self.point(left_true, right_true), right_false))
        if kind == "conditional":
            yes, no = self.condition(node.children[0], incoming)
            yes_true, yes_false = self.condition(node.children[1], yes)
            no_true, no_false = self.condition(node.children[2], no)
            return self.point(yes_true, no_true), self.point(yes_false, no_false)
        # Assignment, equality and method invocation do not retain a child's
        # true/false correlation (JLS 16.1.7-8).
        outgoing = self.value(node, incoming)
        return outgoing, outgoing

    def value(self, node, incoming):
        if node.kind == "conditional":
            yes, no = self.condition(node.children[0], incoming)
            return self.point(self.expression(node.children[1], yes),
                              self.expression(node.children[2], no))
        if node.kind not in {"bool", "integer", "floating", "char", "name", "call",
                             "group", "cast", "unary", "binary", "mutation"}:
            node.token.error("blank final locals require flow rules for this expression domain",
                             code="java.unsupported.blank-final-local")
        for child in node.children:
            incoming = self.expression(child, incoming)
        # The store is after its RHS. Nested writes such as x=(x=1) therefore
        # remove DU before the outer store is checked.
        return self.write(node, incoming) if node.kind == "mutation" else incoming

    def statement(self, node, incoming):
        kind = node.kind
        if kind in {"block", "sequence"}:
            if kind == "block":
                incoming = self.scope_entry(node.children, incoming)
            for child in node.children:
                incoming = self.statement(child, incoming)
            return incoming
        if kind == "var":
            for child in node.children:
                incoming = self.expression(child, incoming)
            bit = self.bits.get(node.symbol, 0)
            # A declaration inside a loop creates a new local on each entry.
            return self.point(incoming, generate=bit) if bit else incoming
        if kind == "assign":
            for child in node.children:
                incoming = self.expression(child, incoming)
            return self.write(node, incoming)
        if kind == "expression":
            return self.expression(node.children[0], incoming)
        if kind == "return":
            for child in node.children:
                incoming = self.expression(child, incoming)
            return None
        if kind == "if":
            condition, yes, no = node.children
            when_true, when_false = self.condition(condition, incoming)
            return self.point(self.statement(yes, when_true), self.statement(no, when_false))
        if kind == "label":
            exit_point = self.point()
            self.targets[id(node)] = {"break": exit_point}
            self.edge(self.statement(node.children[0], incoming), exit_point)
            return exit_point
        if kind == "switch":
            selector, *groups = node.children
            dispatch = self.expression(selector, incoming)
            dispatch = self.scope_entry((child for group in groups for child in group.children), dispatch)
            exit_point = self.point()
            self.targets[id(node)] = {"break": exit_point}
            has_default = any(label.kind == "default" for group in groups for label in group.value)
            previous = None
            for group in groups:
                # Every label is a possible dispatch entry. Constant selector
                # values do not select a single source-flow path.
                state = self.point(dispatch, previous) if node.value == "colon" else dispatch
                for child in group.children:
                    state = self.statement(child, state)
                previous = state
                if node.value == "arrow":
                    self.edge(state, exit_point)
            if node.value == "colon":
                self.edge(previous, exit_point)
            if not has_default:
                self.edge(dispatch, exit_point)
            return exit_point
        if kind in {"while", "for", "do"}:
            if kind == "for":
                initial, condition, update, body = node.children
                incoming = self.statement(initial, incoming)
            else:
                condition, body = node.children
                update = None
            head = self.point(incoming)
            exit_point = self.point()
            continuation = self.point() if kind in {"for", "do"} else head
            self.targets[id(node)] = {"break": exit_point, "continue": continuation}
            if kind == "do":
                self.edge(self.statement(body, head), continuation)
                when_true, when_false = self.condition(condition, continuation)
                self.edge(when_true, head)
            else:
                when_true, when_false = self.condition(condition, head)
                self.edge(self.statement(body, when_true), continuation)
                if update is not None:
                    # Keep an update with no predecessors: Java analyzes it
                    # from vacuous top, and its effects still reach the header.
                    self.edge(self.statement(update, continuation), head)
            self.edge(when_false, exit_point)
            return exit_point
        if kind in {"break", "continue"}:
            self.edge(incoming, self.targets[id(node.control_target)][kind])
            return None
        if kind == "empty":
            return incoming
        node.token.error("blank final locals require flow rules for this statement domain",
                         code="java.unsupported.blank-final-local")

    def check(self, body):
        self.statement(body, self.point(generate=self.universe))
        before = [self.universe] * len(self.points)
        after = [self.universe] * len(self.points)
        work = deque(range(len(self.points)))
        queued = [True] * len(self.points)
        while work:
            index = work.popleft()
            queued[index] = False
            point = self.points[index]
            incoming = self.universe
            for predecessor in point.predecessors:
                incoming &= after[predecessor]
            before[index] = incoming
            outgoing = (incoming | point.generate) & ~point.kill
            if outgoing != after[index]:
                after[index] = outgoing
                for successor in point.successors:
                    if not queued[successor]:
                        queued[successor] = True
                        work.append(successor)
        for index, node, bit in self.writes:
            if not before[index] & bit:
                node.token.error("blank final local is not definitely unassigned before this assignment: "
                                 + node.symbol.name)


def check_blank_final_locals(method):
    if any(binding.is_blank_final for binding in method.locals):
        BlankFinalFlow(method).check(method.body)

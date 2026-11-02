"""Nullable direct-call pointers: source guards, real CFG edges, no runtime.

This domain is not a Rust loan/move lattice. Facts describe possible nullness
and conservative pointee lifetimes, never exclusivity, pointee values or IR
attributes. Parameters start nullable regardless of any observed caller. The
whole supplied source call closure must be non-retaining before IR construction.
"""
from collections import deque
from dataclasses import dataclass, field

from pcc.frontends.go.diagnostics import UnsupportedGo
from pcc.frontends.go.records import underlying
from pcc.frontends.go.types import (
    ArrayType, PointerType, Results, is_string, result_types,
)


NIL, NONNULL, UNKNOWN = 1, 2, 3


@dataclass(frozen=True)
class CallLifetime:
    """An incoming pointee is live for this activation, not forever/global."""
    function: str


@dataclass(frozen=True)
class LocalLifetime:
    function: str
    symbol: object
    depth: int


@dataclass(frozen=True)
class StaticLifetime:
    symbol: object


@dataclass(frozen=True)
class SavedPointer:
    """Analysis-only switch temporary; never an alias to a pointer slot."""
    expression: int


@dataclass(frozen=True)
class PointerFact:
    nulls: int
    lifetimes: frozenset = frozenset()


NULL = PointerFact(NIL)
UNPROVEN = PointerFact(UNKNOWN)


def pointer(dtype):
    return isinstance(underlying(dtype), PointerType)


def join(states):
    """Union possible values; None is unreachable, not a reachable empty map."""
    states = [state for state in states if state is not None]
    if not states:
        return None
    result = dict(states[0])
    for state in states[1:]:
        for symbol in result.keys() | state.keys():
            left, right = result.get(symbol, UNPROVEN), state.get(symbol, UNPROVEN)
            result[symbol] = PointerFact(left.nulls | right.nulls,
                                         left.lifetimes | right.lifetimes)
    return result


@dataclass
class Node:
    kind: str
    value: object = None
    edges: list = field(default_factory=list)
    saved: frozenset = frozenset()


class Graph:
    """Go's existing structured control edges, including dispatch/fallthrough."""
    def __init__(self, function):
        self.nodes = []
        done = self.add('exit')
        self.entry = self.sequence(function.body, done)

    def add(self, kind, value=None, edges=(), saved=frozenset()):
        self.nodes.append(Node(kind, value, list(edges), saved))
        return len(self.nodes) - 1

    def sequence(self, statements, following, breaking=None, continuing=None, falling=None):
        for statement in reversed(statements):
            following = self.statement(statement, following, breaking, continuing, falling)
        return following

    def statement(self, statement, following, breaking, continuing, falling):
        kind = statement.kind
        if kind == 'block':
            return self.sequence(statement.body, following, breaking, continuing, falling)
        if kind in ('break', 'continue', 'fallthrough'):
            target = {'break': breaking, 'continue': continuing, 'fallthrough': falling}[kind]
            assert target is not None, 'Sema must resolve legal control targets'
            return self.add('jump', edges=[target])
        if (kind == 'return' or
                kind == 'expression' and statement.exprs[0].kind == 'builtin_io'
                and statement.exprs[0].value == 'panic'):
            # A real terminal panic has no normal successor. Its argument
            # effects are still evaluated; later edges cannot resurrect it.
            return self.add('action', statement)
        if kind == 'if':
            yes = self.sequence(statement.body, following, breaking, continuing, falling)
            no = self.sequence(statement.otherwise or [], following, breaking, continuing, falling)
            entry = self.add('condition', statement.exprs[0], [yes, no])
            return (self.statement(statement.init, entry, breaking, continuing, falling)
                    if statement.init is not None else entry)
        if kind == 'for':
            header = self.add('jump')
            post = (self.statement(statement.post, header, following, header, falling)
                    if statement.post is not None else header)
            body = self.sequence(statement.body, post, following, post, falling)
            if statement.exprs:
                self.nodes[header] = Node('condition', statement.exprs[0], [body, following])
            else:
                self.nodes[header].edges = [body]
            return (self.statement(statement.init, header, breaking, continuing, falling)
                    if statement.init is not None else header)
        if kind == 'range':
            # A statically empty array/integer/string range has no body edge.
            # Dynamic bounds preserve both edges, including zero iterations;
            # do not infer nonnil facts from an unknown trip count.
            ranged = statement.exprs[0]
            dtype = underlying(ranged.type)
            pointer_range = isinstance(dtype, PointerType)
            if pointer_range:
                dtype = underlying(dtype.element)
            if isinstance(dtype, ArrayType):
                has_body = dtype.count > 0
            elif is_string(ranged.type):
                has_body = ranged.constant is None or bool(ranged.constant)
            else:
                has_body = ranged.constant is None or ranged.constant > 0
            header = self.add('range_check', has_body)
            body = self.sequence(statement.body, header, following, header, falling)
            if statement.init is not None:
                body = self.statement(statement.init, body, following, header, falling)
            self.nodes[header].edges = [body, following]
            uses_element = len(statement.targets) == 2 and not (
                statement.targets[-1].kind == 'name' and statement.targets[-1].value == '_')
            # Check the captured pointer once, before iteration assignments or
            # body rebinding. Its pointee outlives this loop by the existing
            # lexical/call-duration proof. An empty pointer range never loads
            # an element (unlike evaluating an explicit array dereference).
            entry_kind = 'range_pointer' if pointer_range and has_body and uses_element else 'evaluate'
            return (self.add(entry_kind, ranged, [header])
                    if statement.evaluate_range else header)
        if kind == 'switch':
            bodies = [self.add('jump') for _ in statement.cases]
            for index, clause in enumerate(statement.cases):
                fall = bodies[index + 1] if index + 1 < len(bodies) else None
                self.nodes[bodies[index]].edges = [
                    self.sequence(clause.body, following, following, continuing, fall)]
            dispatch = next((body for clause, body in zip(statement.cases, bodies)
                             if clause.kind == 'default'), following)
            selector = statement.exprs[0]
            # A selector is evaluated exactly once; later comparisons use the
            # saved value. This is an evaluation marker, never a cached proof.
            saved = frozenset({id(selector)})
            for clause, body in reversed(list(zip(statement.cases, bodies))):
                for comparison in reversed(clause.exprs):
                    dispatch = self.add('condition', comparison, [body, dispatch], saved)
            entry = self.add('switch_selector', selector, [dispatch])
            return (self.statement(statement.init, entry, breaking, continuing, falling)
                    if statement.init is not None else entry)
        return self.add('action', statement, [following])


class PointerFlow:
    def __init__(self, function, callees):
        self.function = function
        self.callees = callees
        self.graph = Graph(function)
        self.owner = function.abi_symbol
        self.incoming = {symbol: CallLifetime(self.owner) for symbol in function.parameters
                         if pointer(symbol.type)}

    def analyze(self):
        entry = {symbol: (PointerFact(UNKNOWN, frozenset({self.incoming[symbol]}))
                          if symbol in self.incoming else UNPROVEN)
                 for symbol in self.function.locals if pointer(symbol.type)}
        states = [None] * len(self.graph.nodes)
        states[self.graph.entry] = entry
        pending = deque([self.graph.entry])
        queued = {self.graph.entry}
        # Solve first, diagnose only final reachable states. Loop transient
        # states cannot be treated as proofs or cause premature acceptance.
        while pending:
            index = pending.popleft()
            queued.remove(index)
            node = self.graph.nodes[index]
            for target, state in self.transfer(node, states[index], check=False):
                merged = join([states[target], state])
                if merged != states[target]:
                    states[target] = merged
                    if target not in queued:
                        pending.append(target)
                        queued.add(target)
        for node, state in zip(self.graph.nodes, states):
            if state is not None:
                self.transfer(node, state, check=True)

    def require_live(self, fact, token):
        if not fact.lifetimes:
            raise UnsupportedGo(token, 'go.unsupported.pointer-escape',
                                'pointer has no proven live source region')
        for lifetime in fact.lifetimes:
            if isinstance(lifetime, (CallLifetime, LocalLifetime)) and lifetime.function != self.owner:
                raise UnsupportedGo(token, 'go.unsupported.pointer-escape',
                                    'pointer lifetime does not belong to this activation')

    def require_nonnull(self, fact, token):
        if fact.nulls != NONNULL:
            raise UnsupportedGo(token, 'go.unsupported.pointer-nil',
                                'source control flow does not prove this evaluated indirection nonnil')
        self.require_live(fact, token)

    def store(self, state, symbol, fact, check):
        if check and fact.nulls & NONNULL:
            self.require_live(fact, symbol.token)
            for lifetime in fact.lifetimes:
                if isinstance(lifetime, LocalLifetime) and lifetime.depth > symbol.depth:
                    raise UnsupportedGo(symbol.token, 'go.unsupported.pointer-escape',
                                        'pointer may outlive its lexical storage region')
                if isinstance(lifetime, CallLifetime) and symbol.depth < 1:
                    raise UnsupportedGo(symbol.token, 'go.unsupported.pointer-escape',
                                        'incoming pointer cannot outlive its current call')
        state[symbol] = fact

    def address(self, expr, state, check, saved=frozenset()):
        if expr.kind == 'dereference':
            value = self.evaluate(expr.args[0], state, check, saved)
            if check:
                self.require_nonnull(value, expr.token)
            return PointerFact(NONNULL, value.lifetimes)
        if expr.kind in ('name', 'selector'):
            symbol = expr.symbol
            lifetime = (StaticLifetime(symbol) if symbol.package_variable else
                        LocalLifetime(self.owner, symbol, symbol.depth))
            return PointerFact(NONNULL, frozenset({lifetime}))
        if expr.kind == 'field':
            return self.address(expr.args[0], state, check, saved)
        if expr.kind == 'index':
            base, index = expr.args
            if base.addressable:
                fact = self.address(base, state, check, saved)
            else:
                self.evaluate(base, state, check, saved)
                fact = UNPROVEN  # Such an array temporary is not source-addressable.
            self.evaluate(index, state, check, saved)
            return fact
        raise AssertionError('unrecognized typed Go address: ' + expr.kind)

    def evaluate(self, expr, state, check, saved=frozenset()):
        if state is None:
            return UNPROVEN
        if id(expr) in saved:
            # Preserve the captured pointer fact, including an address-valued
            # selector. Do not revisit its index/call/dereference operations.
            # Compound targets and non-pointer switches keep their old path.
            return state.get(SavedPointer(id(expr)), self.pointer_value(expr, state))
        kind = expr.kind
        if kind == 'nil':
            return NULL
        if kind == 'address':
            return self.address(expr.args[0], state, check, saved)
        if kind == 'dereference':
            self.address(expr, state, check, saved)
            return UNPROVEN
        if kind in ('array', 'struct'):
            for item in expr.args:
                self.evaluate(item, state, check, saved)
            return UNPROVEN
        if expr.constant is not None:
            # In particular constant array len/cap does not evaluate its
            # operand. Do not manufacture a nonnull fact for that operand.
            return UNPROVEN
        if kind == 'binary' and expr.value in ('&&', '||'):
            self.branches(expr, state, check, saved)
            return UNPROVEN
        if kind == 'call':
            receiver = ([(expr.receiver, self.evaluate(expr.receiver, state, check, saved))]
                        if expr.receiver is not None else [])
            arguments = [self.evaluate(arg, state, check, saved) for arg in expr.args]
            if expr.indirect_receiver and check:
                self.require_nonnull(arguments[0], expr.token)
            if expr.symbol is None:
                return arguments[0] if pointer(expr.type) else UNPROVEN
            if check:
                target = self.callees.get(expr.symbol.abi_symbol)
                if target is not expr.symbol:
                    raise UnsupportedGo(expr.token, 'go.unsupported.pointer-signatures',
                                        'call target lacks a checked original-source non-retention contract')
                for argument, fact in [*receiver, *zip(expr.args, arguments)]:
                    if pointer(argument.type) and fact.nulls & NONNULL:
                        self.require_live(fact, argument.token)
            return UNPROVEN  # Pointer results are not admitted.
        if kind == 'field':
            if expr.addressable:
                self.address(expr, state, check, saved)
            else:
                self.evaluate(expr.args[0], state, check, saved)
            return UNPROVEN
        if kind in ('name', 'selector'):
            return self.pointer_value(expr, state)
        if kind == 'index' and not isinstance(underlying(expr.args[0].type), str):
            self.address(expr, state, check, saved)
            return UNPROVEN
        for argument in expr.args:
            self.evaluate(argument, state, check, saved)
        return UNPROVEN

    def pointer_value(self, expr, state):
        if expr.kind == 'nil':
            return NULL
        if expr.kind in ('name', 'selector') and pointer(expr.type):
            return state.get(expr.symbol, UNPROVEN)
        if expr.kind == 'call' and expr.symbol is None and pointer(expr.type):
            return self.pointer_value(expr.args[0], state)
        return UNPROVEN

    def refined(self, state, expr, nulls):
        while expr.kind == 'call' and expr.symbol is None and pointer(expr.type):
            expr = expr.args[0]
        if expr.kind == 'nil':
            return dict(state) if nulls & NIL else None
        if expr.kind == 'address':
            return dict(state) if nulls & NONNULL else None
        if expr.kind not in ('name', 'selector') or not pointer(expr.type):
            return dict(state)
        fact = state.get(expr.symbol, UNPROVEN)
        intersection = fact.nulls & nulls
        if not intersection:
            return None
        result = dict(state)
        result[expr.symbol] = PointerFact(intersection,
                                         fact.lifetimes if intersection & NONNULL else frozenset())
        return result

    def branches(self, expr, state, check, saved=frozenset()):
        if state is None:
            return None, None
        if id(expr) in saved:
            check = False  # Saved selector value; no repeated evaluated operation.
        if expr.constant is not None:
            self.evaluate(expr, state, check, saved)
            return (dict(state), None) if bool(expr.constant) else (None, dict(state))
        if (expr.kind == 'call' and expr.symbol is None and len(expr.args) == 1
                and underlying(expr.type) in ('bool', 'untyped bool')
                and underlying(expr.args[0].type) in ('bool', 'untyped bool')):
            # A semantic bool-to-bool conversion preserves truth. This does
            # not recognize user functions by name or infer a callee predicate.
            return self.branches(expr.args[0], state, check, saved)
        if expr.kind == 'unary' and expr.value == '!':
            yes, no = self.branches(expr.args[0], state, check, saved)
            return no, yes
        if expr.kind == 'binary':
            left, right = expr.args
            if expr.value == '&&':
                yes, no = self.branches(left, state, check, saved)
                right_yes, right_no = self.branches(right, yes, check, saved)
                return right_yes, join([no, right_no])
            if expr.value == '||':
                yes, no = self.branches(left, state, check, saved)
                right_yes, right_no = self.branches(right, no, check, saved)
                return join([yes, right_yes]), right_no
            if expr.value in ('==', '!='):
                # Boolean switch selectors are snapshots; nil tests depend
                # only on pointer bindings, which checked calls cannot rebind.
                for value, constant in ((left, right), (right, left)):
                    if constant.type in ('bool', 'untyped bool') and constant.constant is not None:
                        yes, no = self.branches(value, state, check, saved)
                        return (yes, no) if bool(constant.constant) == (expr.value == '==') else (no, yes)
                left_fact = self.evaluate(left, state, check, saved)
                right_fact = self.evaluate(right, state, check, saved)
                # Typed nil conversions and a binding proven nil are ordinary
                # nil operands too; equality never assumes distinct pointers.
                value = right if left_fact.nulls == NIL else left if right_fact.nulls == NIL else None
                if value is not None and pointer(value.type):
                    equal = self.refined(state, value, NIL)
                    unequal = self.refined(state, value, NONNULL)
                    return (equal, unequal) if expr.value == '==' else (unequal, equal)
                if pointer(left.type) and pointer(right.type):
                    # Equality to a known live pointer proves nonnil, not a
                    # distinct allocation or a stronger lifetime. This also
                    # covers pointer switch cases such as `case &value:`.
                    # Inequality conveys no nonnil fact: nil != &value.
                    equal = dict(state)
                    if left_fact.nulls == NONNULL:
                        equal = self.refined(equal, right, NONNULL)
                    if equal is not None and right_fact.nulls == NONNULL:
                        equal = self.refined(equal, left, NONNULL)
                    unequal = dict(state)
                    return (equal, unequal) if expr.value == '==' else (unequal, equal)
                return dict(state), dict(state)
        self.evaluate(expr, state, check, saved)
        return dict(state), dict(state)

    def values(self, expressions, state, check, saved=frozenset()):
        values = [self.evaluate(expr, state, check, saved) for expr in expressions]
        if len(expressions) == 1 and isinstance(expressions[0].type, Results):
            return [UNPROVEN] * len(expressions[0].type.elements)
        return values

    def action(self, statement, state, check):
        kind = statement.kind
        result = dict(state)
        if kind == 'const':
            return result
        if kind == '=':
            # Both assignment phases matter. All destination addresses use
            # PRE-store facts, then all RHS values are captured, then stores
            # happen left-to-right (including duplicate pointer targets).
            for target in statement.targets:
                if not (target.kind == 'name' and target.value == '_'):
                    self.address(target, state, check)
            values = self.values(statement.exprs, state, check)
            for target, fact in zip(statement.targets, values):
                if target.value != '_' and pointer(target.type):
                    self.store(result, target.symbol, fact, check)
            return result
        if kind in ('var', ':='):
            values = self.values(statement.exprs, state, check)
            for index, symbol in enumerate(statement.symbols):
                if symbol.token.text != '_' and pointer(symbol.type):
                    self.store(result, symbol, values[index] if values else NULL, check)
            return result
        if kind in ('++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '&^=', '<<=', '>>='):
            target = statement.targets[0]
            self.address(target, state, check)
            self.evaluate(statement.exprs[0], state, check, frozenset({id(target)}))
            return result
        for expression in statement.exprs:
            self.evaluate(expression, state, check)
        return result

    def transfer(self, node, state, check):
        if state is None:
            return []
        if node.kind == 'condition':
            yes, no = self.branches(node.value, state, check, node.saved)
            return [(node.edges[0], yes), (node.edges[1], no)]
        if node.kind == 'range_check':
            return [(node.edges[0], dict(state) if node.value else None),
                    (node.edges[1], dict(state))]
        if node.kind in ('evaluate', 'range_pointer', 'switch_selector'):
            fact = self.evaluate(node.value, state, check)
            if node.kind == 'range_pointer' and check:
                self.require_nonnull(fact, node.value.token)
            if node.kind == 'switch_selector' and pointer(node.value.type):
                state = dict(state)
                state[SavedPointer(id(node.value))] = fact
        if node.kind == 'action':
            state = self.action(node.value, state, check)
        return [(edge, state) for edge in node.edges]


def validate_pointer_flow(program):
    """Close the source call graph, then validate every body independently.

    Sema already rejects pointer results/aggregate fields/pointees/package
    storage, captures, unsafe operations and unknown/indirect calls. Those
    restrictions are the structural non-retention proof, not inferred LLVM
    attributes. Recursive source calls retain no pointer beyond the call stack.
    """
    callees = {function.abi_symbol: function for function in program.functions}
    assert len(callees) == len(program.functions), 'duplicate owned Go ABI symbol'
    for function in program.functions:
        if any(pointer(dtype) for dtype in result_types(function.result)):
            raise UnsupportedGo(function.token, 'go.unsupported.pointer-signatures',
                                'pointer results have no non-retention contract')
    # Close ALL source call edges, even in an unreachable block. A body cannot
    # advertise non-retention by relying on a missing or unchecked callee.
    def expressions(statement):
        yield from statement.exprs
        yield from statement.targets
        for nested in [statement.init, statement.post]:
            if nested is not None:
                yield from expressions(nested)
        for nested in statement.body + (statement.otherwise or []) + statement.cases:
            yield from expressions(nested)

    for function in program.functions:
        pending = [expr for statement in function.body for expr in expressions(statement)]
        seen = set()
        while pending:
            expr = pending.pop()
            if id(expr) in seen:
                continue
            seen.add(id(expr))
            pending.extend(expr.args)
            if expr.receiver is not None:
                pending.append(expr.receiver)
            if expr.kind == 'call' and expr.symbol is not None:
                if callees.get(expr.symbol.abi_symbol) is not expr.symbol:
                    raise UnsupportedGo(expr.token, 'go.unsupported.pointer-signatures',
                                        'call target lacks a checked original-source non-retention contract')
    for function in program.functions:
        PointerFlow(function, callees).analyze()

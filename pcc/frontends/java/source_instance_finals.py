"""JLS 8.3.1.2/16.9 blank instance fields on the bound class-model AST.

The constructor and instance-initializer checks share the scalar blank-final
control-flow graph. Assignment and unassignment are separate must facts, not
complements. All identities are the actual declared SourceField objects; no
allocation, reference representation or constructor emission is implied.
"""
from .source_blank_final import BlankFinalFlow
from .source_syntax import SourceField, SourceMethod


def blank_instance_final(field):
    return ("static" not in field.modifiers and "final" in field.modifiers
            and field.initializer is None)


class InstanceFinalFlow(BlankFinalFlow):
    def __init__(self, fields):
        super().__init__()
        self.extended_domain = True
        self.fields = tuple(fields)
        self.bits = {id(field): 1 << i for i, field in enumerate(self.fields)}
        self.universe = (1 << len(self.fields)) - 1
        self.reads = []
        self.exits = []

    def binding_bit(self, binding):
        # SourceField is mutable semantic metadata, not a hashable local slot.
        return self.bits.get(id(binding), 0)

    def initial(self, *, da=0, du=None):
        if du is None:
            du = self.universe
        return self.point(kill=self.universe & ~du, da_kill=self.universe & ~da)

    def value(self, node, incoming):
        if node.kind in {"instance_field", "static_field", "receiver_static_field", "receiver_call",
                         "class_new", "this", "string"}:
            if node.current_receiver is not None:
                incoming = self.expression(node.current_receiver, incoming)
            for child in node.children:
                incoming = self.expression(child, incoming)
            bit = self.binding_bit(node.symbol) if node.kind == "instance_field" else 0
            if bit:
                self.reads.append((incoming, node.token, node.symbol, bit))
            return incoming
        return super().value(node, incoming)

    def statement(self, node, incoming):
        if node.kind in {"field_assign", "array_assign", "constructor_call", "throw", "return"}:
            if node.current_receiver is not None:
                incoming = self.expression(node.current_receiver, incoming)
            for child in node.children:
                incoming = self.expression(child, incoming)
            if node.kind == "field_assign":
                # The binder admits only simple stores to a blank final of
                # this class in the constructor/initializer context. This
                # LHS is not a read and the write happens after its RHS.
                return self.write(node, incoming)
            if node.kind == "return":
                self.exits.append((incoming, node.token))
                return None
            if node.kind == "throw":
                return None
            return incoming
        return super().statement(node, incoming)

    def verify(self, outgoing):
        du_before, du_after = self.solve()
        _, da_after = self.solve(da=True)

        def fact(facts, point):
            # Abrupt completion has no normal successor; it satisfies both
            # properties vacuously, as do impossible boolean outcomes.
            return self.universe if point is None else facts[point]

        for index, node, bit in self.writes:
            if not du_before[index] & bit:
                node.token.error("blank final instance field is not definitely unassigned before this assignment: "
                                 + node.symbol.token.text)
        for index, token, field, bit in self.reads:
            if not fact(da_after, index) & bit:
                token.error("blank final instance field is not definitely assigned before this read: "
                            + field.token.text)
        for index, token in self.exits:
            missing = self.universe & ~fact(da_after, index)
            if missing:
                field = next(field for field in self.fields if missing & self.binding_bit(field))
                token.error("blank final instance field is not definitely assigned before constructor completes: "
                            + field.token.text)
        return fact(da_after, outgoing), fact(du_after, outgoing)


def check_blank_instance_finals(program, constructors):
    fields = tuple(field for field in program.fields if blank_instance_final(field))
    if not fields:
        return

    # Instance variable initializers and initializer blocks share the same
    # state, in declaration order. A blank field has no implicit source write:
    # its default runtime value does not make it definitely assigned.
    initializer = InstanceFinalFlow(fields)
    outgoing = initializer.initial()
    for step in program.initialization_steps:
        outgoing = (initializer.expression(step.initializer, outgoing) if isinstance(step, SourceField)
                    else initializer.statement(step, outgoing))
    initializer_da, initializer_du = initializer.verify(outgoing)

    for constructor in constructors:
        flow = InstanceFinalFlow(fields)
        children = constructor.body.children
        incoming = flow.initial()
        if children and children[0].kind == "constructor_call":
            # Check the actual expressions in the invocation before replacing
            # the receiver facts. Existing Sema forbids access to this before
            # super/this invocation (JLS 8.8.7.1).
            incoming = flow.statement(children[0], incoming)
            children = children[1:]
        delegated = isinstance(constructor.constructor_target, SourceMethod)
        # After an alternate this(...) invocation Java assumes all the blank
        # fields assigned. Its selected constructor is checked independently,
        # and the bound graph has already rejected delegation cycles. After
        # super(...) the shared instance-initializer state applies once.
        da = flow.universe if delegated else initializer_da
        du = 0 if delegated else initializer_du
        incoming = flow.point(incoming, generate=du, kill=flow.universe & ~du,
                              da_generate=da, da_kill=flow.universe & ~da)
        for child in children:
            incoming = flow.statement(child, incoming)
        if constructor.body.completes:
            flow.exits.append((incoming, constructor.token))
        flow.verify(incoming)

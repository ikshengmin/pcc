"""Function-local Go labels, branch targets and lexical goto legality.

Labels have their own function namespace. They never create a value scope or
permit entry into a block. Ordinary control edges remain visible to the same
pointer proof and IR owners as structured loops and expression switches.
"""

from pcc.frontends.go.diagnostics import GoDiagnostic


def walk(statements):
    for statement in statements:
        yield statement
        if statement.init is not None:
            yield from walk([statement.init])
        yield from walk(statement.body)
        yield from walk(statement.otherwise or [])
        yield from walk(statement.cases)
        if statement.post is not None:
            yield from walk([statement.post])


def unlabeled(statement):
    while statement.kind == 'label' and len(statement.body) == 1:
        statement = statement.body[0]
    return statement


def position(token):
    return token.line, token.column


class Branches:
    def __init__(self, function):
        self.labels = {}
        self.used = set()
        self.snapshots = {}
        self.declarations = []
        self.gotos = []
        self.back_edges = []
        for statement in walk(function.body):
            if statement.kind != 'label' or statement.label.text == '_':
                continue
            name = statement.label.text
            if name in self.labels:
                raise GoDiagnostic(statement.label, 'label already defined: ' + name)
            self.labels[name] = statement
        self.resolve(function.body, [])
        for name, label in self.labels.items():
            if name not in self.used:
                raise GoDiagnostic(label.label, 'label defined and not used: ' + name)

    def resolve(self, statements, controls):
        for statement in statements:
            kind = statement.kind
            if kind in ('goto', 'break', 'continue'):
                label = None
                if statement.label is not None:
                    name = statement.label.text
                    label = self.labels.get(name)
                    if label is None:
                        raise GoDiagnostic(statement.label, 'undefined label: ' + name)
                    self.used.add(name)
                if kind == 'goto':
                    statement.control_target = label
                    self.gotos.append(statement)
                    if position(label.token) <= position(statement.token):
                        self.back_edges.append((position(label.token), position(statement.token)))
                else:
                    allowed = ('for', 'range', 'switch') if kind == 'break' else ('for', 'range')
                    if label is not None:
                        # Only the directly labeled statement is a target;
                        # a label labeling another label is not a for/switch.
                        target = label.body[0] if len(label.body) == 1 else None
                        if (target is None or target.kind not in allowed or
                                not any(target is control for control in controls)):
                            raise GoDiagnostic(statement.label, 'invalid ' + kind + ' label: ' + name)
                    else:
                        target = next((control for control in reversed(controls)
                                       if control.kind in allowed), None)
                        if target is None:
                            raise GoDiagnostic(statement.token, kind + ' outside loop')
                    statement.control_target = target
            nested = [*controls, statement] if kind in ('for', 'range', 'switch') else controls
            if statement.init is not None:
                self.resolve([statement.init], controls)
            self.resolve(statement.body, nested)
            self.resolve(statement.otherwise or [], nested)
            self.resolve(statement.cases, nested)
            if statement.post is not None:
                self.resolve([statement.post], controls)

    def capture(self, statement, scopes):
        # Retain the actual dictionaries as identity-bearing block objects.
        # Snapshot symbols now, before later declarations mutate those dicts.
        variables = frozenset(symbol for scope in scopes for symbol in scope.values()
                              if symbol.constant is None)
        barriers = frozenset(id(declaration) for scope, declaration in self.declarations
                             if any(scope is current for current in scopes))
        self.snapshots[id(statement)] = tuple(scopes), variables, barriers

    def declaration(self, statement, scope):
        # An actual var _ declaration still forms a barrier without a named
        # symbol. An empty var () group has no variable declaration and is
        # deliberately excluded by the caller.
        self.declarations.append((scope, statement))

    def validate(self):
        for branch in self.gotos:
            source_scopes, source_variables, source_barriers = self.snapshots[id(branch)]
            target_scopes, target_variables, target_barriers = self.snapshots[id(branch.control_target)]
            if (len(target_scopes) > len(source_scopes) or
                    any(left is not right for left, right in zip(source_scopes, target_scopes))):
                raise GoDiagnostic(branch.token, 'goto ' + branch.label.text + ' jumps into a block')
            missing = target_variables - source_variables
            if missing:
                symbol = min(missing, key=lambda item: position(item.token))
                raise GoDiagnostic(branch.token, 'goto ' + branch.label.text +
                                   ' jumps over declaration of ' + symbol.token.text)
            if target_barriers - source_barriers:
                raise GoDiagnostic(branch.token, 'goto ' + branch.label.text +
                                   ' jumps over variable declaration')

    def reentered(self, token):
        # A backward goto crossing a declaration can recreate its variable in
        # the same lexical region. Reusing one addressable slot would erase
        # that fresh identity. Keep the existing repeated-storage frontier;
        # scalar values need no new allocation or runtime owner.
        point = position(token)
        return any(start <= point <= end for start, end in self.back_edges)

    def breaks(self, statement):
        return any(child.kind == 'break' and child.control_target is statement
                   for child in walk(statement.body + statement.cases))

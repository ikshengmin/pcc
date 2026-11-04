"""Declaration-owned associated types for concrete local trait implementations.

Each required type has its own abstract identity. Concrete aliases are checked
before method signatures, then substituted through the existing signature and
body pipeline. There is no new layout, runtime, solver or instantiation owner.
Generic projections, alias-to-alias projections and concrete DSTs remain gaps.
"""
from dataclasses import replace

from .generics import bounded_type, children
from .types import TypeApplication, TypeParameter


class AssociatedTypes:
    def __init__(self, traits):
        self.traits = traits
        self.declarations = {}
        self.parameters = {}
        self.values = {}

    def prepare(self):
        markers = self.traits.checker.standard_markers
        for trait in self.traits.declarations:
            name = trait.token.text
            items = {}
            for index, item in enumerate(trait.associated_types):
                if index >= 128:
                    raise item.token.error('associated type count exceeds implementation limit (128)')
                member = item.token.text
                if member in items:
                    raise item.token.error('duplicate trait associated type')
                items[member] = item
                parameter = TypeParameter('associated type ' + name, index, 'Self::' + member)
                self.parameters[name, member] = parameter
                # Associated types are not unqualified lexical type variables.
                # In `type Copy: Copy`, the bound must still resolve the standard
                # trait, rather than mistaking the item name for a shadowing T.
                bound_item = replace(item, token=replace(item.token, text=parameter.name))
                markers.prepare(bound_item, {parameter.name: parameter})
            self.declarations[name] = items

    def owners(self, trait):
        return (trait, *sorted(self.traits.hierarchy.ancestors[trait]))

    def has_types(self, trait):
        return any(self.declarations[owner] for owner in self.owners(trait))

    def scope(self, trait, owner=None):
        """Resolve only unambiguous Self::X in the trait's declared hierarchy."""
        candidates = {}
        for declared in self.owners(trait):
            for member in self.declarations[declared]:
                candidates.setdefault(member, []).append(declared)
        result = {}
        for member, declarations in candidates.items():
            if len(declarations) != 1:
                # A declaration may inherit ambiguous names without using them.
                # Leave an actual use at the explicit projection capability gate.
                continue
            declared = declarations[0]
            parameter = self.parameters[declared, member]
            result['Self::' + member] = (parameter if owner is None else
                                          self.values[declared, owner][parameter])
        return result

    def register(self, implementation):
        checker = self.traits.checker
        trait, owner = implementation.trait, implementation.owner
        required = self.declarations[trait]
        aliases = {}
        for item in implementation.associated_types:
            name = item.token.text
            if name in aliases:
                raise item.token.error('duplicate trait implementation associated type')
            if name not in required:
                raise item.token.error('associated type is not a member of the implemented trait')
            aliases[name] = item
        missing = required.keys() - aliases.keys()
        if missing:
            raise implementation.token.error('missing required trait associated types: ' + ', '.join(sorted(missing)))
        values = {}
        for name, item in aliases.items():
            bounded_type(item.value, item.token)
            pending = [item.value]
            while pending:
                raw = pending.pop()
                path = raw.name if isinstance(raw, TypeApplication) else raw
                if isinstance(path, str) and path.startswith('Self::'):
                    raise item.token.error('associated alias-to-alias projections and cycles are not implemented')
                pending.extend(children(raw))
            value = checker.generics.normalize(item.value, item.token, {'Self': owner})
            checker.valid_type(value, item.token, allow_reference=False)
            parameter = self.parameters[trait, name]
            obligations = checker.standard_markers.constraints[parameter]
            if not checker.standard_markers.satisfies(obligations, value):
                raise item.token.error('associated type does not satisfy its canonical marker bounds')
            values[parameter] = value
        self.values[trait, owner] = values

    def bindings(self, trait, owner):
        result = {}
        for declared in self.owners(trait):
            result.update(self.values[declared, owner])
        return result

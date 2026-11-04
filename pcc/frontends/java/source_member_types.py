"""Native static member-class identity, lexical scope and access (JLS 6/8/13).

Canonical source names and binary owners are distinct. Every member class
joins the supplied source closure; none is discarded because it is uncalled.
No enclosing instance, constructor, inherited declaration or runtime class
metadata is synthesized by this source resolver.
"""
from .diagnostics import JavaError


class StaticMemberTypes:
    def initialize_member_types(self, programs):
        self.top_levels = tuple(sorted(programs, key=lambda item: item.name))
        self.classes, self.canonical_classes, self.member_types = {}, {}, {}

        def retain(program):
            if program.source_name in self.canonical_classes:
                raise JavaError("duplicate source class " + program.source_name.replace("/", "."))
            if program.name in self.classes:
                raise JavaError("duplicate source binary class " + program.name.replace("/", "."))
            self.classes[program.name] = program
            self.canonical_classes[program.source_name] = program
            members = {}
            self.member_types[program.name] = members
            for member in program.member_classes:
                if member.simple_name in members:
                    member.token.error("duplicate member class " + member.simple_name)
                members[member.simple_name] = member
                retain(member)

        for program in self.top_levels:
            retain(program)
        self.programs = tuple(sorted(self.classes.values(), key=lambda item: item.name))
        # Only top-level declarations can conflict with package prefixes.
        # Outer.Inner is a member type, never the package Outer.
        top_names = {program.name for program in self.top_levels}
        for name in top_names:
            pieces = name.split("/")
            for end in range(1, len(pieces)):
                if "/".join(pieces[:end]) in top_names:
                    raise JavaError("source class/package name conflict: " + name.replace("/", "."))

    def lexical_classes(self, program):
        while True:
            yield program
            if program.enclosing_name is None:
                return
            program = self.classes[program.enclosing_name]

    def nest_root(self, program):
        for owner in self.lexical_classes(program):
            pass
        return owner

    def same_nest(self, caller, target):
        return self.nest_root(caller) is self.nest_root(target)

    def member_accessible(self, caller, target, modifiers, *, import_context=False):
        if "private" in modifiers:
            return not import_context and self.same_nest(caller, target)
        return caller.package == target.package or "public" in modifiers

    def require_class_access(self, caller, target, token, *, import_context=False):
        for owner in self.lexical_classes(target):
            if not self.member_accessible(caller, owner, owner.modifiers, import_context=import_context):
                token.error("source class is not accessible: " + owner.source_name.replace("/", "."))

    def visible_simple_type(self, caller, name):
        for owner in self.lexical_classes(caller):
            if owner.simple_name == name:
                return owner
            member = self.member_types[owner.name].get(name)
            if member is not None:
                return member
        imported = self.imports[caller.name].get(name)
        if imported is not None:
            return imported
        qualified = caller.package + "/" + name if caller.package else name
        same_package = self.canonical_classes.get(qualified)
        if same_package is not None:
            return same_package
        implicit = self.canonical_classes.get("java/lang/" + name)
        return implicit if implicit is not None and "public" in implicit.modifiers else None

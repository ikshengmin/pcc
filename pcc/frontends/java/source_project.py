"""Closed-world Java source type/import and static-method identity resolution.

Only supplied source classes are visible; there is no classpath, classfile,
reflection lookup or host JDK fallback. Single-type and single-static imports,
including accessible static member types, resolve against supplied declarations
before lowering. Native static member classes retain their lexical type scope
and distinct binary owners. Inheritance, inner classes and on-demand imports
remain explicit unsupported domains.
"""
from .diagnostics import UnsupportedJava
from .source_sema import SourceAnalyzer
from .source_types import select_method
from .source_member_types import StaticMemberTypes


# Ordinary source classes inherit these method names from Object. The source
# model does not supply their actual definitions, but may not bypass their
# lexical scope by selecting an imported overload instead (JLS 6.3/6.4.1).
OBJECT_INHERITED_METHOD_NAMES = frozenset((
    "equals", "hashCode", "getClass", "toString", "clone", "finalize",
    "notify", "notifyAll", "wait",
))


class SourceProject(StaticMemberTypes):
    def __init__(self, programs, *, analyzer_type=SourceAnalyzer):
        self.initialize_member_types(programs)
        self.imports = {}
        self.static_types = {}
        self.static_fields = {}
        self.static_methods = {}
        self.analyzers = {}
        for program in self.top_levels:
            imported = {}
            for tokens in program.imports:
                name = "/".join(token.text for token in tokens)
                target = self.canonical_classes.get(name)
                if target is None:
                    target = self.external_type_dependency(program, tokens)
                    if target is None:
                        raise UnsupportedJava("source-project dependency resolution requires supplied source for "
                                              + name.replace("/", "."), code="java.unsupported.source-dependency")
                else:
                    if not target.package:
                        tokens[0].error("cannot import a type from the unnamed package")
                    self.require_class_access(program, target, tokens[0], import_context=True)
                simple = tokens[-1].text
                if (simple in imported and imported[simple] is not target or
                        simple == program.simple_name and target is not program):
                    tokens[-1].error("conflicting single-type import " + simple)
                imported[simple] = target
            # A single-static import can contribute to all three namespaces.
            # Bind its type now, before declaration resolution; fields and
            # methods need the analyzer tables built below. Distinct static
            # imports may contribute same-named types; ambiguity is checked
            # only when that type name is used (JLS 7.5.3/6.5.5.1).
            static_types = {}
            for tokens in program.static_imports:
                owner = self.static_import_owner(program, tokens)
                target = self.static_import_type(program, owner, tokens[-1].text)
                if target is None:
                    continue
                simple = tokens[-1].text
                if (simple in imported and imported[simple] is not target or
                        simple == program.simple_name and target is not program):
                    tokens[-1].error("conflicting single-static type import " + simple)
                static_types.setdefault(simple, {})[target.name] = target
            self.imports[program.name] = imported
            self.static_types[program.name] = static_types
        for program in self.programs:
            # Imports belong to the compilation unit, outside every class body.
            # A member type may shadow one without creating a top-level import
            # conflict or gaining private access from its declaration's nest.
            self.imports[program.name] = self.imports[self.nest_root(program).name]
            self.static_types[program.name] = self.static_types[self.nest_root(program).name]
        # Resolve declaration identities before building overload tables. The
        # native scalar owner has no extra declaration domain; model subclasses
        # may bind retained source type names without granting lowering.
        for program in self.programs:
            self.resolve_declaration_types(program)
        for program in self.programs:
            analyzer = analyzer_type(program, self)
            analyzer.declare_methods()
            analyzer.declare_fields()
            self.analyzers[program.name] = analyzer
        # Imports may name declarations in a later source unit. Build every
        # member table first, then validate imports even when never used.
        for program in self.programs:
            self.resolve_static_imports(program)

    def static_import_owner(self, program, tokens):
        owner = "/".join(token.text for token in tokens[:-1])
        target = self.canonical_classes.get(owner)
        if target is None:
            tokens[0].error("static import requires supplied source for " + owner.replace("/", "."),
                            code="java.unsupported.source-dependency")
        if not target.package:
            tokens[0].error("cannot import a static member from the unnamed package")
        self.require_class_access(program, target, tokens[0], import_context=True)
        return target

    def static_import_type(self, program, owner, name):
        target = self.member_types[owner.name].get(name)
        if (target is not None and "static" in target.modifiers and
                self.member_accessible(program, target, target.modifiers, import_context=True)):
            return target
        return None

    def resolve_static_imports(self, program):
        fields, methods = {}, {}
        for tokens in program.static_imports:
            target = self.static_import_owner(program, tokens)
            analyzer = self.analyzers[target.name]
            name = tokens[-1].text
            imported_type = self.static_import_type(program, target, name)
            # The import is outside all class bodies: even a private member
            # of this compilation unit's own class cannot be imported here.
            def accessible(member):
                return ("static" in member.modifiers and
                        self.member_accessible(program, target, member.modifiers, import_context=True))

            field = analyzer.fields.get(name)
            imported_field = field is not None and accessible(field)
            candidates = [method for method in analyzer.methods.values()
                          if method.token.text == name and accessible(method)]
            if not imported_field and not candidates and imported_type is None:
                tokens[-1].error("no accessible static member for single-static import "
                                 + target.source_name.replace("/", ".") + "." + name)
            if imported_field:
                fields.setdefault(name, {})[target.name] = field
            for method in candidates:
                # Repeated imports of the same declaration are harmless, but
                # same-signature declarations from different owners remain
                # separate overload candidates and may be ambiguous at use.
                methods.setdefault(name, {})[target.name, method.parameter_types] = method
        self.static_fields[program.name] = fields
        self.static_methods[program.name] = methods

    def simple_field(self, analyzer, name, token):
        for owner in self.lexical_classes(analyzer.program):
            field = self.analyzers[owner.name].fields.get(name)
            if field is not None:
                return field
        imported = self.static_fields[analyzer.program.name].get(name, {})
        if len(imported) > 1:
            token.error("ambiguous single-static imported field " + name)
        return next(iter(imported.values()), None)

    def resolve_declaration_types(self, program):
        pass

    def external_type_dependency(self, program, tokens):
        # Only a nonlowering semantic model may describe an unresolved type.
        return None

    def resolve_call(self, analyzer, node):
        pieces = node.value.split(".")
        if len(pieces) == 1:
            # JLS 6.4.1/15.12.1: a member method shadows every imported method
            # with the same name, including when no member overload applies.
            imported = self.static_methods[analyzer.program.name].get(node.value, {})
            for owner in self.lexical_classes(analyzer.program):
                methods = self.analyzers[owner.name].methods
                if any(method.token.text == node.value for method in methods.values()):
                    return select_method(methods, node)
                if node.value in OBJECT_INHERITED_METHOD_NAMES and (owner.enclosing_name is not None or imported):
                    node.token.error("inherited Object method lookup cannot be replaced by an enclosing or imported method",
                                     code="java.unsupported.object-inherited-methods")
            return select_method(imported, node)
        target = self.resolve_qualified_owner(analyzer, node)
        caller = analyzer.program
        methods = self.analyzers[target.name].methods
        # JLS 15.12.2.1 considers only accessible potentially applicable methods.
        # An inaccessible narrower overload must not defeat a public widening
        # overload that is otherwise applicable to this call.
        accessible = {key: method for key, method in methods.items()
                      if self.member_accessible(caller, target, method.modifiers)}
        method = select_method(accessible, node)
        if method is None:
            if select_method(methods, node) is not None:
                node.token.error("source static method is not accessible: " + node.value)
            node.token.error("unresolved source static method " + node.value)
        return method

    def resolve_field(self, analyzer, node):
        caller = analyzer.program
        if "." not in node.value:
            field = self.simple_field(analyzer, node.value, node.token)
            target = self.classes[field.owner] if field is not None else caller
        else:
            target = self.resolve_qualified_owner(analyzer, node)
            field = self.analyzers[target.name].fields.get(node.value.rsplit(".", 1)[-1])
        if field is None:
            node.token.error("undefined local variable or constant field " + node.value)
        if not self.member_accessible(caller, target, field.modifiers):
            node.token.error("source constant field is not accessible: " + node.value)
        return field

    def resolve_qualified_owner(self, analyzer, node):
        pieces = node.value.split(".")
        caller = analyzer.program
        # JLS 6.5.2 expression-name reclassification precedes package/type
        # lookup. An int local obscures an identically named class/package.
        if (any(pieces[0] in scope for scope in analyzer.scopes)
                or self.simple_field(analyzer, pieces[0], node.token) is not None):
            node.token.error("primitive local/parameter/field cannot be dereferenced as a source class")
        first = self.visible_simple_type(caller, pieces[0], node.token)
        qualifier = pieces[:-1]
        if first is not None:
            target = first
            self.require_class_access(caller, target, node.token)
            for name in qualifier[1:]:
                owner = self.analyzers[target.name]
                # JLS 6.5.2 reclassifies every intermediate name, not just
                # the first one. A member value/method obscures a type even
                # when the member cannot be accessed from this caller.
                if (name in owner.fields or any(method.token.text == name for method in owner.methods.values())
                        or name in OBJECT_INHERITED_METHOD_NAMES):
                    node.token.error("source field or method cannot be dereferenced as a member type")
                member = self.member_types[target.name].get(name)
                if member is None:
                    node.token.error("unresolved source member type " + name)
                target = member
                self.require_class_access(caller, target, node.token)
        else:
            # The full bootstrap java.lang public-type directory is not yet
            # provided by the shared Java class metadata contract. A matching
            # supplied package path cannot prove that its first component is
            # not an implicit type (for example System.Helper.f()). Never
            # silently reinterpret that unknown name as a package.
            if len(qualifier) > 1:
                node.token.error("package-qualified calls require complete implicit-type metadata; "
                                 "supply a single-type import and a resolved class qualifier", code="java.unsupported.implicit-type-metadata")
            node.token.error("unresolved source class qualifier " + ".".join(qualifier))
        self.require_class_access(caller, target, node.token)
        return target

    def analyze(self):
        for analyzer in self.analyzers.values():
            for field in analyzer.program.fields:
                analyzer.evaluate_field(field)
        for analyzer in self.analyzers.values():
            analyzer.analyze()
        return self

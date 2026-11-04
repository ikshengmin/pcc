"""Closed-world Java source type/import and static-method identity resolution.

Only supplied source classes are visible; there is no classpath, classfile,
reflection lookup or host JDK fallback. Single-type and single-static imports
resolve against supplied declarations before lowering. Inheritance/member
types and on-demand imports remain explicit unsupported domains.
"""
from .diagnostics import JavaError, UnsupportedJava
from .source_sema import SourceAnalyzer
from .source_types import select_method


# Ordinary source classes inherit these method names from Object. The source
# model does not supply their actual definitions, but may not bypass their
# lexical scope by selecting an imported overload instead (JLS 6.3/6.4.1).
OBJECT_INHERITED_METHOD_NAMES = frozenset((
    "equals", "hashCode", "getClass", "toString", "clone", "finalize",
    "notify", "notifyAll", "wait",
))


class SourceProject:
    def __init__(self, programs, *, analyzer_type=SourceAnalyzer):
        self.programs = tuple(sorted(programs, key=lambda item: item.name))
        self.classes = {}
        self.imports = {}
        self.static_fields = {}
        self.static_methods = {}
        self.analyzers = {}
        for program in self.programs:
            if program.name in self.classes:
                raise JavaError("duplicate source class " + program.name.replace("/", "."))
            self.classes[program.name] = program
        # JLS 7.1: a package member cannot simultaneously name a top-level
        # class and a subpackage. Check the entire supplied closure, not order.
        for name in self.classes:
            pieces = name.split("/")
            for end in range(1, len(pieces)):
                if "/".join(pieces[:end]) in self.classes:
                    raise JavaError("source class/package name conflict: " + name.replace("/", "."))
        for program in self.programs:
            imported = {}
            for tokens in program.imports:
                name = "/".join(token.text for token in tokens)
                target = self.classes.get(name)
                if target is None:
                    target = self.external_type_dependency(program, tokens)
                    if target is None:
                        raise UnsupportedJava("source-project dependency resolution requires supplied source for "
                                              + name.replace("/", "."), code="java.unsupported.source-dependency")
                else:
                    self.require_class_access(program, target, tokens[0])
                simple = tokens[-1].text
                if (simple in imported and imported[simple] is not target or
                        simple == program.name.rsplit("/", 1)[-1] and target is not program):
                    tokens[-1].error("conflicting single-type import " + simple)
                imported[simple] = target
            self.imports[program.name] = imported
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

    def resolve_static_imports(self, program):
        fields, methods = {}, {}
        for tokens in program.static_imports:
            owner = "/".join(token.text for token in tokens[:-1])
            target = self.classes.get(owner)
            if target is None:
                tokens[0].error("static import requires supplied source for " + owner.replace("/", "."),
                                code="java.unsupported.source-dependency")
            self.require_class_access(program, target, tokens[0])
            analyzer = self.analyzers[owner]
            name = tokens[-1].text
            # The import is outside all class bodies: even a private member
            # of this compilation unit's own class cannot be imported here.
            def accessible(member):
                return ("static" in member.modifiers and "private" not in member.modifiers
                        and (program.package == target.package or "public" in member.modifiers))

            field = analyzer.fields.get(name)
            imported_field = field is not None and accessible(field)
            candidates = [method for method in analyzer.methods.values()
                          if method.token.text == name and accessible(method)]
            if not imported_field and not candidates:
                tokens[-1].error("no accessible static member for single-static import "
                                 + owner.replace("/", ".") + "." + name)
            if imported_field:
                fields.setdefault(name, {})[owner] = field
            for method in candidates:
                # Repeated imports of the same declaration are harmless, but
                # same-signature declarations from different owners remain
                # separate overload candidates and may be ambiguous at use.
                methods.setdefault(name, {})[owner, method.parameter_types] = method
        self.static_fields[program.name] = fields
        self.static_methods[program.name] = methods

    def simple_field(self, analyzer, name, token):
        field = analyzer.fields.get(name)
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

    @staticmethod
    def require_class_access(caller, target, token):
        if caller.package != target.package and "public" not in target.modifiers:
            token.error("source class is not accessible: " + target.name.replace("/", "."))

    def visible_simple_type(self, caller, name):
        if caller.name.rsplit("/", 1)[-1] == name:
            return caller
        imported = self.imports[caller.name].get(name)
        if imported is not None:
            return imported
        qualified = caller.package + "/" + name if caller.package else name
        same_package = self.classes.get(qualified)
        if same_package is not None:
            return same_package
        implicit = self.classes.get("java/lang/" + name)
        # JLS 7.3 imports only public java.lang top-level types. A hidden
        # type must not obscure an otherwise usable top-level package name.
        return implicit if implicit is not None and "public" in implicit.modifiers else None

    def resolve_call(self, analyzer, node):
        pieces = node.value.split(".")
        if len(pieces) == 1:
            # JLS 6.4.1/15.12.1: a member method shadows every imported method
            # with the same name, including when no member overload applies.
            own_name = any(method.token.text == node.value for method in analyzer.methods.values())
            imported = self.static_methods[analyzer.program.name].get(node.value, {})
            if not own_name and imported and node.value in OBJECT_INHERITED_METHOD_NAMES:
                node.token.error("inherited Object method lookup cannot be replaced by a single-static import",
                                 code="java.unsupported.object-inherited-methods")
            methods = (analyzer.methods if own_name else
                       imported)
            return select_method(methods, node)
        target = self.resolve_qualified_owner(analyzer, node)
        caller = analyzer.program
        methods = self.analyzers[target.name].methods
        # JLS 15.12.2.1 considers only accessible potentially applicable methods.
        # An inaccessible narrower overload must not defeat a public widening
        # overload that is otherwise applicable to this call.
        accessible = {key: method for key, method in methods.items()
                      if target is caller or "private" not in method.modifiers and
                      (caller.package == target.package or "public" in method.modifiers)}
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
        if target is not caller and ("private" in field.modifiers or
                                    caller.package != target.package and "public" not in field.modifiers):
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
        first = self.visible_simple_type(caller, pieces[0])
        qualifier = pieces[:-1]
        if first is not None:
            if len(qualifier) != 1:
                node.token.error("member types require the nested-class source domain", code="java.unsupported.member-types")
            target = first
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

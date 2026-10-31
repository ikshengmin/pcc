"""Closed-world Java source type/import and static-method identity resolution.

Only supplied source classes are visible; there is no classpath, classfile,
reflection lookup or host JDK fallback. Single-type imports and resolved type-qualified calls are resolved before lowering. Inheritance/member types and
static/on-demand imports remain explicit unsupported domains.
"""
from .diagnostics import JavaError, UnsupportedJava
from .source_sema import SourceAnalyzer
from .source_types import select_method


class SourceProject:
    def __init__(self, programs):
        self.programs = tuple(sorted(programs, key=lambda item: item.name))
        self.classes = {}
        self.imports = {}
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
                    raise UnsupportedJava("source-project dependency resolution requires supplied source for "
                                          + name.replace("/", "."))
                self.require_class_access(program, target, tokens[0])
                simple = tokens[-1].text
                if (simple in imported and imported[simple] is not target or
                        simple == program.name.rsplit("/", 1)[-1] and target is not program):
                    tokens[-1].error("conflicting single-type import " + simple)
                imported[simple] = target
            self.imports[program.name] = imported
            analyzer = SourceAnalyzer(program, self)
            analyzer.declare_methods()
            self.analyzers[program.name] = analyzer

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
            return select_method(analyzer.methods, node)
        caller = analyzer.program
        # JLS 6.5.2 expression-name reclassification precedes package/type
        # lookup. An int local obscures an identically named class/package.
        if any(pieces[0] in scope for scope in analyzer.scopes):
            node.token.error("int local/parameter cannot be dereferenced as a source class")
        first = self.visible_simple_type(caller, pieces[0])
        qualifier = pieces[:-1]
        if first is not None:
            if len(qualifier) != 1:
                node.token.error("member types require the nested-class source domain", True)
            target = first
        else:
            # The full bootstrap java.lang public-type directory is not yet
            # provided by the shared Java class metadata contract. A matching
            # supplied package path cannot prove that its first component is
            # not an implicit type (for example System.Helper.f()). Never
            # silently reinterpret that unknown name as a package.
            if len(qualifier) > 1:
                node.token.error("package-qualified calls require complete implicit-type metadata; "
                                 "supply a single-type import and a resolved class qualifier", True)
            node.token.error("unresolved source class qualifier " + ".".join(qualifier))
        self.require_class_access(caller, target, node.token)
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

    def analyze(self):
        for analyzer in self.analyzers.values():
            analyzer.analyze()
        return self

"""Load real codegen bodies into a host recording model, without PCC imports.

Unlike runtime_body_model, codegen has mixin methods and constructor state,
not exported runtime functions and define_* memory cells. Follow self helpers
across the explicitly supplied mixins, and same-module function/constant
dependencies in their own namespaces. Imports can only reach supplied source
modules. Explicit recording sinks stop the method closure; named roots always
use production bodies. Module and class initialization never runs.
"""

import __future__
import ast
import builtins
import copy
from pathlib import Path
from types import MethodType, SimpleNamespace


class CodegenBodyModel:
    def __init__(self, sources, mixins, namespace, *, sinks=(), constant_factories=()):
        self.sources = {name: Path(path) for name, path in sources.items()}
        self.trees = {
            name: ast.parse(path.read_text(encoding="utf-8"))
            for name, path in self.sources.items()
        }
        self.namespaces = {
            name: dict(namespace, __builtins__=dict(
                vars(builtins), __import__=self._import,
            ))
            for name in sources
        }
        self.definitions = {}
        self.ambiguous_globals = {}
        self.loaded_globals = set()
        self.methods = {}
        self.loaded = {}
        self.loaded_nodes = []
        self.sinks = set(sinks)
        self.constant_factories = set(constant_factories)
        for module, tree in self.trees.items():
            definitions = self.definitions[module] = {}
            ambiguous = self.ambiguous_globals[module] = set()
            declarations = {}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    definitions[node.name] = node
                elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                    if isinstance(node.targets[0], ast.Name):
                        definitions[node.targets[0].id] = node
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    if isinstance(node.target, ast.Name):
                        definitions[node.target.id] = node
                elif isinstance(node, ast.ClassDef) and node.name in mixins.get(module, ()):
                    for method in node.body:
                        if isinstance(method, ast.FunctionDef):
                            assert method.name not in self.methods, method.name
                            self.methods[method.name] = (module, method)
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    bound = {node.name}
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    bound = {alias.asname or alias.name.split(".")[0] for alias in node.names}
                else:
                    bound = {child.id for child in ast.walk(node)
                             if isinstance(child, ast.Name)
                             and isinstance(child.ctx, (ast.Store, ast.Del))}
                for name in bound:
                    if name in declarations or definitions.get(name) is not node:
                        ambiguous.add(name)
                    declarations[name] = node

    def _execute(self, module, node, *, method=False):
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                raise ValueError("decorated codegen model body: " + node.name)
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None and not self._safe_constant(module, default):
                    raise ValueError("unsupported codegen model default: " + node.name)
        tree = ast.fix_missing_locations(ast.Module(
            body=[copy.deepcopy(node)], type_ignores=[],
        ))
        # A class body binds methods in class locals, never in module globals.
        # Preserve that distinction while retaining the real globals seen by
        # each function body, including a same-named module helper.
        bindings = {} if method else self.namespaces[module]
        exec(compile(tree, str(self.sources[module]), "exec",
                     flags=__future__.annotations.compiler_flag, dont_inherit=True),
             self.namespaces[module], bindings)
        return bindings

    def _dependencies(self, module, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id in self.definitions[module]:
                    self.function(module, child.id)
            elif isinstance(child, ast.ImportFrom) and child.module in self.sources:
                for alias in child.names:
                    self.function(child.module, alias.name)

    def _safe_constant(self, module, value, seen=frozenset()):
        try:
            ast.literal_eval(value)
            return True
        except (ValueError, TypeError):
            pass
        if isinstance(value, ast.Name):
            if value.id in seen:
                return False
            definition = self.definitions[module].get(value.id)
            return isinstance(definition, (ast.Assign, ast.AnnAssign)) and self._safe_constant(
                module, definition.value, seen | {value.id},
            )
        if isinstance(value, ast.Call) and not value.keywords:
            if isinstance(value.func, ast.Attribute):
                owner = value.func.value
                if isinstance(owner, ast.Name):
                    factory = owner.id + "." + value.func.attr
                    if factory in self.constant_factories:
                        return all(self._safe_constant(module, arg, seen) for arg in value.args)
                if value.func.attr == "as_pointer" and not value.args:
                    return self._safe_constant(module, owner, seen)
        return False

    def function(self, module, name):
        """Load a source function or constant, with module-local dependencies."""
        namespace = self.namespaces[module]
        if name in self.ambiguous_globals[module]:
            raise ValueError("ambiguous codegen model declaration: " + module + "." + name)
        if (module, name) in self.loaded_globals:
            return namespace[name]
        node = self.definitions[module][name]
        # Bind functions before following references, so recursion is finite.
        if isinstance(node, ast.FunctionDef):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    self._dependencies(module, default)
            self._execute(module, node)
            self.loaded_globals.add((module, name))
            self.loaded_nodes.append(node)
            self._dependencies(module, node)
        else:
            if not self._safe_constant(module, node.value):
                raise ValueError("unsupported codegen model constant: " + module + "." + name)
            self._dependencies(module, node)
            self._execute(module, node)
            self.loaded_globals.add((module, name))
        return namespace[name]

    def _import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level or not fromlist or name not in self.sources:
            raise ImportError("codegen model has no source provider for " + name)
        return SimpleNamespace(**{
            item: self.function(name, item) for item in fromlist
        })

    def load_methods(self, names):
        roots = set(names)
        pending = list(roots)
        while pending:
            name = pending.pop()
            if name in self.loaded or (name in self.sinks and name not in roots):
                continue
            module, node = self.methods[name]
            self._dependencies(module, node)
            self.loaded[name] = self._execute(module, node, method=True)[name]
            self.loaded_nodes.append(node)
            for child in ast.walk(node):
                if (isinstance(child, ast.Attribute)
                        and isinstance(child.value, ast.Name)
                        and child.value.id == "self"
                        and child.attr in self.methods):
                    pending.append(child.attr)
        return dict(self.loaded)

    def initialize_state(self, host, module, initializer):
        """Fill absent fields from simple, unconditional constructor defaults.

        Only literal values and empty builtin containers are eligible. Target,
        IR, environment-derived and branch-dependent state remains explicit
        model input. Do not execute the compiler constructor or its helpers.
        """
        _, method = self.methods[initializer]
        assert self.methods[initializer][0] == module
        needed = set()
        for body in self.loaded_nodes:
            for child in ast.walk(body):
                if (isinstance(child, ast.Attribute)
                        and isinstance(child.value, ast.Name)
                        and child.value.id in ("self", "host")):
                    needed.add(child.attr)
                elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                      and child.func.id in ("getattr", "hasattr") and len(child.args) >= 2
                      and isinstance(child.args[0], ast.Name)
                      and child.args[0].id in ("self", "host")
                      and isinstance(child.args[1], ast.Constant)):
                    needed.add(child.args[1].value)
        assignments = {}
        initializers = {}
        for statement in method.body:
            for child in ast.walk(statement):
                if (isinstance(child, ast.Attribute) and isinstance(child.ctx, (ast.Store, ast.Del))
                        and isinstance(child.value, ast.Name) and child.value.id == "self"):
                    assignments.setdefault(child.attr, []).append(statement)
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target, value = statement.targets[0], statement.value
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                target, value = statement.target, statement.value
            else:
                continue
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                initializers[target.attr] = (statement, value)
        defaults = {}
        for name in sorted(needed.intersection(assignments)):
            if hasattr(host, name):
                continue
            declaration = initializers.get(name)
            if declaration is None or assignments[name] != [declaration[0]]:
                # A conditional-only assignment is still a declaration. Do
                # not let getattr's fallback hide its unmodelled branch.
                raise ValueError("ambiguous model state: " + name)
            value = declaration[1]
            try:
                initial = ast.literal_eval(value)
            except (ValueError, TypeError):
                if not (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in ("set", "dict", "list", "tuple")
                        and not value.args and not value.keywords):
                    raise ValueError("explicit model state required: " + name)
                initial = getattr(builtins, value.func.id)()
            defaults[name] = initial
        for name, initial in defaults.items():
            setattr(host, name, initial)

    def bind(self, host, names):
        for name, method in self.load_methods(names).items():
            setattr(host, name, MethodType(method, host))

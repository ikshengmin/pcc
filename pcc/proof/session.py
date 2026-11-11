"""Checking modules: loading, imports and the built-in declarations."""

from __future__ import annotations

import os

from pcc.proof.elaborate import BUILTIN_SCOPE, ElabError, Elaborator
from pcc.proof.kernel import Kernel
from pcc.proof.surface import SurfaceError, read_module
from pcc.proof.terms import Const, Leaf, Pi, Sort, Split, Var


class CheckError(Exception):
    def __init__(self, path, line, message):
        super().__init__(f"{path}:{line}: {message}")
        self.path = path
        self.line = line
        self.message = message


def _repository_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Session:
    """One kernel and the modules checked against it."""

    prelude_module = "pcc.proof.prelude"
    builtin_module = "pcc.proof.runtime"

    def __init__(self, roots=()):
        self.kernel = Kernel()
        self.operators = {}
        self.fields = {"zero": 0, "suc": 1, "false": 0, "true": 0}
        self.modules = {}
        self.reports = {}
        self.roots = [os.path.abspath(root) for root in roots]
        self.roots.append(_repository_root())
        self._loading = []
        self._bootstrap()

    def _bootstrap(self):
        """Naturals, booleans and the conditional, declared through the kernel."""
        kernel = self.kernel
        nat = Const("Nat")
        boolean = Const("Bool")
        kernel.add_data("Nat", 0, Sort(0), [("zero", nat), ("suc", Pi("n", nat, nat))])
        kernel.add_data("Bool", 0, Sort(0), [("false", boolean), ("true", boolean)])
        # ite : Pi (A: Type) (b: Bool) (x: A) (y: A). A, with ite A true x y = x.
        ite_type = Pi("A", Sort(0), Pi("b", boolean, Pi("x", Var(1), Pi("y", Var(2), Var(3)))))
        ite_tree = Split(1, (("false", Leaf(Var(0))), ("true", Leaf(Var(1)))))
        kernel.add_function("ite", ite_type, 4, ite_tree)
        self.modules[self.builtin_module] = dict(BUILTIN_SCOPE)

    # -- locating modules ----------------------------------------------------

    def find(self, module):
        relative = module.replace(".", os.sep)
        for root in self.roots:
            for candidate in (
                os.path.join(root, relative + ".py"),
                os.path.join(root, relative, "__init__.py"),
            ):
                if os.path.isfile(candidate):
                    return candidate
        return None

    def module_name(self, path):
        """The dotted name of ``path``, and the root that name is relative to."""
        path = os.path.abspath(path)
        directory = os.path.dirname(path)
        parts = [os.path.splitext(os.path.basename(path))[0]]
        while os.path.isfile(os.path.join(directory, "__init__.py")):
            parts.append(os.path.basename(directory))
            directory = os.path.dirname(directory)
        if parts[0] == "__init__":
            parts = parts[1:]
        return ".".join(reversed(parts)), directory

    # -- checking ------------------------------------------------------------

    def load(self, module, importer="<import>", line=0):
        """The exported scope of ``module``, checking it first if needed."""
        if module in self.modules:
            return self.modules[module]
        if module in self._loading:
            raise CheckError(importer, line, f"import cycle through {module}")
        path = self.find(module)
        if path is None:
            raise CheckError(importer, line, f"cannot find module {module}")
        return self._check(path, module)

    def check_file(self, path):
        """Check the module at ``path``; returns its dotted name."""
        module, root = self.module_name(path)
        if root not in self.roots:
            self.roots.insert(0, root)
        if module not in self.modules:
            self._check(os.path.abspath(path), module)
        return module

    def _check(self, path, module):
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
        except OSError as error:
            raise CheckError(path, 0, f"cannot read: {error.strerror}")
        try:
            surface = read_module(source, module, path)
        except SurfaceError as error:
            raise CheckError(error.path, error.line, error.message)
        self._loading.append(module)
        try:
            if module != self.prelude_module and self.prelude_module not in self._loading:
                self.load(self.prelude_module, path, 0)
            elaborator = Elaborator(self, module, path)
            try:
                elaborator.run(surface)
            except ElabError as error:
                raise CheckError(path, error.line, error.message)
            if not self.kernel.idle:
                raise CheckError(path, 0, "internal error: a declaration was left unfinished")
            self.modules[module] = elaborator.scope
            self.reports[module] = (path, list(elaborator.checked))
        finally:
            self._loading.pop()
        return self.modules[module]

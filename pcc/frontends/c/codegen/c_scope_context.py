"""Explicit scope-transition contexts for the C code generator."""

from __future__ import annotations

from collections import ChainMap


class NewScopeContext:
    """Enter and restore one lexical declaration scope."""

    def __init__(self, codegen) -> None:
        self._codegen = codegen

    def __enter__(self):
        codegen = self._codegen
        self._old_scope_id = codegen._current_scope_id
        codegen._scope_id_counter += 1
        codegen._current_scope_id = codegen._scope_id_counter
        codegen.env = codegen.env.new_child()
        codegen._decl_ast_types = codegen._decl_ast_types.new_child()
        codegen._typedef_ast_types = codegen._typedef_ast_types.new_child()
        return codegen

    def __exit__(self, exc_type, exc, traceback) -> None:
        codegen = self._codegen
        codegen.env = codegen.env.parents
        codegen._decl_ast_types = codegen._decl_ast_types.parents
        codegen._typedef_ast_types = codegen._typedef_ast_types.parents
        codegen._current_scope_id = self._old_scope_id


class NewFunctionContext:
    """Enter and restore one function-lowering scope."""

    def __init__(self, codegen) -> None:
        self._codegen = codegen

    def __enter__(self):
        codegen = self._codegen
        self._old_function = codegen.function
        self._old_display_name = codegen._function_display_name
        self._old_frame_address_marker = codegen._frame_address_marker
        self._old_builder = codegen.builder
        self._old_environment = codegen.env
        self._old_decl_ast_types = codegen._decl_ast_types
        self._old_typedef_ast_types = codegen._typedef_ast_types
        self._old_labels = codegen._labels
        self._old_scope_id = codegen._current_scope_id
        codegen.in_global = False
        codegen._scope_id_counter += 1
        codegen._current_scope_id = codegen._scope_id_counter
        codegen.env = codegen.env.new_child()
        codegen._decl_ast_types = codegen._decl_ast_types.new_child()
        codegen._typedef_ast_types = codegen._typedef_ast_types.new_child()
        codegen._labels = {}
        codegen._frame_address_marker = None
        return codegen

    def __exit__(self, exc_type, exc, traceback) -> None:
        codegen = self._codegen
        codegen.function = self._old_function
        codegen._function_display_name = self._old_display_name
        codegen._frame_address_marker = self._old_frame_address_marker
        codegen.builder = self._old_builder
        codegen.env = self._old_environment
        codegen._decl_ast_types = self._old_decl_ast_types
        codegen._typedef_ast_types = self._old_typedef_ast_types
        codegen._labels = self._old_labels
        codegen._current_scope_id = self._old_scope_id
        codegen.in_global = True


class UnevaluatedDeclarationScope:
    """Bind a type query's declarations in a fresh scope, emitting nothing.

    The builder is cleared so no initializer, bound, layout or value can be
    emitted. On exit the builder, the unevaluated binding depth and the scope
    counter are restored, after the inner scope has been left.

    An explicit class, not a ``@contextmanager`` method: pcc applies a
    decorated method by calling ``contextlib.contextmanager`` at class
    definition, which pcc's stdlib does not implement, so a native pcc1 could
    not initialize the C code generator.
    """

    def __init__(self, codegen) -> None:
        self._codegen = codegen
        self._scope = None
        self._counter = 0
        self._builder = None
        self._depth = 0

    def __enter__(self):
        codegen = self._codegen
        self._counter = codegen._scope_id_counter
        self._builder = codegen.builder
        self._depth = getattr(codegen, "_unevaluated_binding_depth", 0)
        scope = codegen.new_scope()
        try:
            scope.__enter__()
        except BaseException:
            self._restore()
            raise
        self._scope = scope
        # No initializer, bound, layout or value emission is permitted.
        codegen.builder = None
        codegen._unevaluated_binding_depth = self._depth + 1
        return None

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self._scope.__exit__(exc_type, exc, traceback)
        finally:
            self._restore()

    def _restore(self) -> None:
        codegen = self._codegen
        codegen.builder = self._builder
        codegen._unevaluated_binding_depth = self._depth
        codegen._scope_id_counter = self._counter


class TypedefDefinitionScope:
    """Resolve an existing alias in its declaring scope, not a later shadow.

    An explicit class for the same reason as UnevaluatedDeclarationScope.
    """

    def __init__(self, codegen, name: str) -> None:
        self._codegen = codegen
        self._name = name
        self._env = None
        self._aliases = None

    def __enter__(self):
        codegen = self._codegen
        name = self._name
        env, aliases = codegen.env, codegen._typedef_ast_types
        key = "__typedef_" + name
        env_index = next((i for i, scope in enumerate(env.maps) if key in scope), 0)
        alias_index = next((i for i, scope in enumerate(aliases.maps) if name in scope), 0)
        self._env, self._aliases = env, aliases
        try:
            codegen.env = ChainMap(*env.maps[env_index:])
            codegen._typedef_ast_types = ChainMap(*aliases.maps[alias_index:])
        except BaseException:
            codegen.env, codegen._typedef_ast_types = env, aliases
            raise
        return None

    def __exit__(self, exc_type, exc, traceback) -> None:
        codegen = self._codegen
        codegen.env, codegen._typedef_ast_types = self._env, self._aliases

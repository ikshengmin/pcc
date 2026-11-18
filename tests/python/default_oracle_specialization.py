"""Restricted proof of the exact-False default route, not a general optimizer."""
import ast
import copy

FLAG = 'owned_matcher_only'
SCRATCH = {'sparse_positions', 'sparse_cursor', 'scan'}

def _bool(node):
    return isinstance(node, ast.Constant) and type(node.value) is bool

class _DefaultFalse(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id == FLAG:
            assert isinstance(node.ctx, ast.Load), 'default flag is assigned'
            return ast.copy_location(ast.Constant(False), node)
        return node

    def visit_UnaryOp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.op, ast.Not) and _bool(node.operand):
            return ast.copy_location(ast.Constant(not node.operand.value), node)
        return node

    def visit_Compare(self, node):
        node = self.generic_visit(node)
        if (len(node.ops) == 1 and isinstance(node.ops[0], ast.IsNot)
                and _bool(node.left) and _bool(node.comparators[0])):
            return ast.copy_location(ast.Constant(node.left.value is not node.comparators[0].value), node)
        return node

    def visit_BoolOp(self, node):
        # Visit in source order. Never discard an unknown prefix: its calls,
        # comparisons and truth conversions must still execute.
        values = []
        for original in node.values:
            value = self.visit(original)
            if _bool(value):
                stopping = (isinstance(node.op, ast.And) and not value.value) or (
                    isinstance(node.op, ast.Or) and value.value)
                if stopping:
                    if not values:
                        return ast.copy_location(ast.Constant(value.value), node)
                    values.append(value)
                    break
                # A leading/intermediate neutral constant has no side effects.
                # A final neutral constant must remain: it determines the value.
                if original is node.values[-1]:
                    values.append(value)
            else:
                values.append(value)
        if not values:
            return ast.copy_location(ast.Constant(isinstance(node.op, ast.And)), node)
        if len(values) == 1:
            return ast.copy_location(values[0], node)
        node.values = values
        return node

    def visit_IfExp(self, node):
        test = self.visit(node.test)
        if _bool(test):
            return self.visit(node.body if test.value else node.orelse)
        node.test = test
        node.body = self.visit(node.body)
        node.orelse = self.visit(node.orelse)
        return node

    def visit_If(self, node):
        test = self.visit(node.test)
        if _bool(test):
            result = []
            for stmt in node.body if test.value else node.orelse:
                visited = self.visit(stmt)
                if isinstance(visited, list): result.extend(visited)
                elif visited is not None: result.append(visited)
            return result
        node.test = test
        # NodeTransformer handles flattening nested replacement statement lists.
        for field in ('body', 'orelse'):
            result = []
            for stmt in getattr(node, field):
                visited = self.visit(stmt)
                if isinstance(visited, list): result.extend(visited)
                elif visited is not None: result.append(visited)
            setattr(node, field, result)
        return node


def specialize_default(function):
    function = copy.deepcopy(function)
    args = function.args
    assert not args.posonlyargs and not args.kwonlyargs and args.vararg is None and args.kwarg is None
    assert [a.arg for a in args.args] == ['fragments', 'semantics', 'span', 'mode', FLAG]
    assert all(a.annotation is None for a in args.args[:4])
    assert isinstance(args.args[-1].annotation, ast.Name) and args.args[-1].annotation.id == 'bool'
    assert isinstance(args.args[-1].annotation.ctx, ast.Load)
    assert len(args.defaults) == 1 and _bool(args.defaults[0]) and args.defaults[0].value is False
    assert not function.decorator_list and function.returns is None
    assert not any(isinstance(n, ast.Name) and n.id == FLAG and not isinstance(n.ctx, ast.Load)
                   for n in ast.walk(function)), 'default flag is assigned'
    args.args.pop(); args.defaults.clear()
    function = _DefaultFalse().visit(function)
    assert not any(isinstance(n, ast.Name) and n.id in SCRATCH | {FLAG}
                   for n in ast.walk(function)), 'default route retains private machinery'
    return ast.fix_missing_locations(function)


def _same(node, source):
    return ast.dump(node, include_attributes=False) == ast.dump(ast.parse(source).body[0], include_attributes=False)

def _validate_snapshot_projection(function):
    # The historical stripper is intentionally retained, but its broader
    # matching rules must never erase unrelated statements or side effects.
    writes = [n for n in ast.walk(function) if isinstance(n, ast.Name)
              and n.id == 'changed' and isinstance(n.ctx, (ast.Store, ast.Del))]
    assert len(writes) == 2
    assert len(function.body) == 2 and isinstance(function.body[1], ast.Try)
    scope = function.body[1]
    assert _same(scope.body[0], 'changed = False')
    loops = [n for n in scope.body if isinstance(n, ast.While)
             and ast.dump(n.test) == ast.dump(ast.parse('index < len(p.ids)', mode='eval').body)]
    assert len(loops) == 1
    commits = [n for n in loops[0].body if isinstance(n, ast.If)
               and isinstance(n.test, ast.Name) and n.test.id == 'consumed']
    assert len(commits) == 1 and _same(commits[0].body[0], 'changed = True')
    expected = ast.parse('if not changed and fragments.spans.length(span) == len(p.ids):\n    result = fragments.new_fragment()\n    fragments.extend_fragment(result, span)\n    return result').body[0]
    assert ast.dump(scope.body[-2], include_attributes=False) == ast.dump(expected, include_attributes=False)
    assert _same(scope.body[-1], 'return p.finish()')


def _reject_rebinding(tree):
    assert not any(isinstance(n, ast.Name) and n.id == '_run'
                   and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(tree))
    assert sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               and n.name == '_run' for n in ast.walk(tree)) == 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert all((alias.asname or alias.name.split('.')[0]) != '_run' for alias in node.names)
            assert all(alias.name != '*' for alias in node.names)


def assert_frozen_default(source_text, frozen_test_text, *, corrected=False, runtime_function=None):
    tree = ast.parse(source_text)
    _reject_rebinding(tree)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run']
    assert len(functions) == 1
    if runtime_function is not None:
        import inspect
        assert inspect.isfunction(runtime_function) and runtime_function.__name__ == '_run'
        assert runtime_function.__code__.co_firstlineno == functions[0].lineno
        assert type(runtime_function.__defaults__) is tuple and len(runtime_function.__defaults__) == 1
        assert runtime_function.__defaults__[0] is False and runtime_function.__kwdefaults__ is None
        from types import CodeType
        module_code = compile(source_text, runtime_function.__code__.co_filename, 'exec', dont_inherit=True)
        expected_codes = [value for value in module_code.co_consts
                          if isinstance(value, CodeType) and value.co_name == '_run']
        assert len(expected_codes) == 1 and runtime_function.__code__ == expected_codes[0]
    restored = specialize_default(functions[0])
    _validate_snapshot_projection(restored)
    frozen = ast.parse(frozen_test_text)
    original_test = next(n for n in frozen.body if isinstance(n, ast.FunctionDef)
                         and n.name == 'test_frozen_oracle_is_exact_except_for_noop_publication')
    strip = next(n for n in original_test.body if isinstance(n, ast.ClassDef) and n.name == 'StripSnapshot')
    namespace = {'ast': ast}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(strip)], type_ignores=[])),
                 '<unchanged frozen StripSnapshot>', 'exec'), namespace)
    restored = namespace['StripSnapshot']().visit(restored)
    restored.name = '_legacy_run'
    legacy_source = next(ast.literal_eval(n.value) for n in frozen.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == '_LEGACY_RUN_SOURCE' for t in n.targets))
    expected = ast.parse(legacy_source).body[0]
    if corrected:
        from tests.python.frozen_safety_deltas import corrected_frozen
        expected = corrected_frozen(legacy_source)
    assert ast.dump(restored, include_attributes=False) == ast.dump(expected, include_attributes=False)

from copy import deepcopy
import pytest
from pcc.frontends.python.codegen import vthread_effect_analysis as a
from pcc.frontends.python import pipeline as p


def restore(surface):
    result=deepcopy(surface)
    for module,rows in result.items():
        for name,info in rows.items():
            info.setdefault('owning_module',module)
            info.setdefault('export_name',name)
    return result


@pytest.mark.parametrize('flag',[False,True,None,0,1,'', 'yes',[],[1]])
def test_normalized_flags_and_public_default(flag):
    exports={'leaf':{'f':{'kind':'function','may_park':flag,'semantic_decorator':flag},
                     'C':{'kind':'class','methods':({'name':'quiet'},{'name':'work','may_park':flag},3)},
                     'skip':{'kind':'not-callable'}},'empty':{}}
    before=deepcopy(exports)
    full=a.closed_world_vthread_effect_export_surface(exports)
    compact=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=True)
    assert exports==before
    assert restore(compact)==full
    assert list(compact)==list(full)
    assert list(compact['leaf'])==list(full['leaf'])
    assert 'may_park' in full['leaf']['f']
    assert compact['leaf']['f']['may_park'] is bool(flag)
    assert 'owning_module' not in compact['leaf']['f']
    assert 'export_name' not in compact['leaf']['f']
    assert compact['leaf']['f']['semantic_decorator'] is bool(flag)
    assert [m['name'] for m in compact['leaf']['C']['methods']]==['quiet','work']
    assert a._export_function_target(full,'leaf','f')==a._export_function_target(compact,'leaf','f')
    assert a._export_target_is_may_park(full,('leaf','f'))==a._export_target_is_may_park(compact,('leaf','f'))


@pytest.mark.parametrize('fail_at',[None,'owner','export','decorator','park','method'])
def test_original_producer_callbacks_errors_and_order(fail_at):
    outcomes=[]
    for compact in [False,True]:
        events=[]
        class Value:
            def __init__(self,label):self.label=label
            def __str__(self):
                events.append(('str',self.label))
                if fail_at==self.label:raise ValueError(self.label)
                return self.label
            def __bool__(self):
                events.append(('bool',self.label))
                if fail_at==self.label:raise ValueError(self.label)
                return self.label=='park'
        class Record(dict):
            def get(self,key,*args):events.append(('get',key));return super().get(key,*args)
        exports={'m':{'f':Record(kind='function',owning_module=Value('owner'),export_name=Value('export'),semantic_decorator=Value('decorator'),may_park=Value('park')),
                      'C':Record(kind='class',methods=(Record(name=Value('method'),may_park=False),))}}
        try:
            result=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=compact)
            outcome=('ok',restore(result))
        except Exception as exc:outcome=('error',type(exc).__name__,exc.args)
        outcomes.append((outcome,events))
    assert outcomes[0]==outcomes[1]


@pytest.mark.parametrize('exports',[{'m':{'x':None}}, {'m':{'x':{}}}, {'m':{'C':{'kind':'class','methods':None}}}, {'m':None}])
def test_incomplete_and_malformed_fields(exports):
    outcomes=[]
    for compact in [False,True]:
        try:outcome=('ok',restore(a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=compact)))
        except Exception as exc:outcome=('error',type(exc).__name__,exc.args)
        outcomes.append(outcome)
    assert outcomes[0]==outcomes[1]


@pytest.mark.parametrize('case',['cross-module','duplicate'])
def test_existing_summary_semantics_with_compact_projection(monkeypatch,tmp_path,case):
    from tests.python import test_virtual_thread_park_effect as existing
    original=a.closed_world_vthread_effect_export_surface
    monkeypatch.setattr(a,'closed_world_vthread_effect_export_surface',lambda exports:original(exports,compact_redundant_names=True))
    if case=='cross-module':existing.test_summary_metadata_matches_eager_across_compiled_siblings(tmp_path)
    else:existing.test_summary_duplicate_definition_uses_last_binding(tmp_path)


@pytest.mark.parametrize('hook',['none','projector','writer','reader','builder'])
def test_only_owned_private_worker_projection_is_compacted(monkeypatch,hook):
    captured={}
    def collect(*args,**kwargs):captured.update(kwargs);return 'done'
    monkeypatch.setattr(p._pipeline_frontend_parallel,'build_shared_exports',collect)
    original=p._closed_world_vthread_effect_export_surface
    if hook=='projector':monkeypatch.setattr(p,'_closed_world_vthread_effect_export_surface',lambda exports:exports)
    if hook=='writer':monkeypatch.setattr(p,'_write_native_exports_wire',lambda *args,**kwargs:None)
    if hook=='reader':monkeypatch.setattr(p,'_read_native_exports_wire',lambda *args,**kwargs:({},{}))
    if hook=='builder':monkeypatch.setattr(p,'_build_closed_world_vthread_effect_summary',lambda *args: {})
    result=p._build_python_frontend_shared_exports_parallel('',[],[],[],[],entry_module='',sibling_inits=(),ir_scaffold_mode='on',verbose=False,max_parallel=1,oversized_chunk_count=0,safe_parallel=1)
    assert result=='done'
    chosen=captured['vthread_effect_export_surface']
    if hook=='none':
        assert chosen is p._summary_worker_export_surface
        assert captured['write_effect_exports_wire'] is p._vthread_effect_summary_wire.write_effect_exports
        assert chosen({'m':{'f':{'kind':'function'}}})['m']['f']=={'kind':'function','semantic_decorator':False,'may_park':False}
    else:
        assert chosen is p._closed_world_vthread_effect_export_surface
        assert captured['write_effect_exports_wire'] is None
    assert 'may_park' in original({'m':{'f':{'kind':'function'}}})['m']['f']


@pytest.mark.parametrize('kind',['function','class'])
def test_temporary_input_finalizer_order_is_unchanged(kind):
    outcomes=[]
    for compact in [False,True]:
        events=[]
        class Method(dict):
            def __init__(self):super().__init__(name='work',may_park=False)
            def __del__(self):events.append('method')
        class Info(dict):
            def __init__(self):super().__init__(kind=kind)
            def get(self,key,*args):
                if key=='methods':return (Method(),)
                return super().get(key,*args)
            def __del__(self):events.append('info')
        class Exports:
            def items(self):return iter((('entry',Info()),))
            def __del__(self):events.append('exports')
        class Input:
            def items(self):return iter((('module',Exports()),))
        result=a.closed_world_vthread_effect_export_surface(Input(),compact_redundant_names=compact)
        outcomes.append((restore(result),tuple(events)))
    assert outcomes[0]==outcomes[1]
    assert 'exports' in outcomes[0][1] and 'info' in outcomes[0][1]
    if kind=='class':assert 'method' in outcomes[0][1]


@pytest.mark.parametrize('module_key,export_key',[('m','f'),('','f'),('m',''),('','')])
@pytest.mark.parametrize('owner,name',[('m','f'),('other','renamed'),('', ''),(None,None),(17,23)])
def test_names_defaults_aliases_and_empty_values(module_key,export_key,owner,name):
    exports={module_key:{export_key:{'kind':'function','owning_module':owner,'export_name':name,'may_park':True}}}
    full=a.closed_world_vthread_effect_export_surface(exports)
    compact=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=True)
    assert restore(compact)==full
    assert a._export_function_target(full,module_key,export_key)==a._export_function_target(compact,module_key,export_key)


def test_custom_string_keys_do_not_gain_equality_callbacks():
    events=[]
    class Key(str):
        __hash__=str.__hash__
        def __eq__(self,other):
            events.append('eq')
            raise AssertionError('unexpected custom equality')
    module=Key('m');name=Key('f')
    exports={module:{name:{'kind':'function'}}}
    result=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=True)
    assert events==[]
    row=next(iter(next(iter(result.values())).values()))
    assert row['owning_module']=='m' and row['export_name']=='f'


@pytest.mark.parametrize('relative,owner_alias,name_alias',[(False,False,False),(False,False,True),(False,True,False),(False,True,True),(True,True,True)])
def test_class_alias_method_summary_positive_and_negative(relative,owner_alias,name_alias):
    from pcc.frontends.python.parser import parse
    prefix='pkg.' if relative else ''
    leaf=prefix+'leaf';owner=prefix+'actual' if owner_alias else leaf
    canonical='RealWorker' if name_alias else 'Worker';module_name=prefix+'case'
    source='from '+('.leaf' if relative else 'leaf')+' import Worker\ndef call_method():\n    Worker().run()\n    Worker().quiet()\n'
    module=parse(source,'case.py')
    # The production context assigns the manifest's module name when lifting.
    module=type(module)(module_name,module.body,module.docstring)
    info={'kind':'class','owning_module':owner,'export_name':canonical,'methods':({'name':'run','may_park':True},{'name':'quiet','may_park':False})}
    exports={leaf:{'Worker':info}};exports.setdefault(owner,{})[canonical]=info
    full=a.closed_world_vthread_effect_export_surface(exports)
    compact=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=True)
    summary=a.build_closed_world_vthread_effect_summary(module,module_name,full)
    assert summary==a.build_closed_world_vthread_effect_summary(module,module_name,compact)
    key=a._vthread_effect_method_key(owner,canonical,'run',full)
    assert key in str(summary)
    for field,needed in [('owning_module',owner_alias),('export_name',name_alias)]:
        if needed:
            mutant=deepcopy(full);del mutant[leaf]['Worker'][field]
            assert a.build_closed_world_vthread_effect_summary(module,module_name,mutant)!=summary


@pytest.mark.parametrize('module_name,class_name',[('', ''),('leaf',''),('', 'Worker')])
def test_empty_class_name_method_key_fallback(module_name,class_name):
    exports={module_name:{class_name:{'kind':'class','methods':({'name':'run','may_park':True},)}}}
    full=a.closed_world_vthread_effect_export_surface(exports)
    compact=a.closed_world_vthread_effect_export_surface(exports,compact_redundant_names=True)
    assert a._vthread_effect_method_key(module_name,class_name,'run',full)==a._vthread_effect_method_key(module_name,class_name,'run',compact)


def test_private_effect_rows_decode_only_accessed_modules_and_preserve_summary(tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline_exports, vthread_effect_summary_wire as wire
    from pcc.frontends.python.py_lift import parse_and_lift

    native = {
        "pkg.leaf": {"park": {"kind": "function", "may_park": True}},
        "pkg.owner": {"Worker": {"kind": "class", "methods": (
            {"name": "run", "may_park": True}, {"name": "quiet", "may_park": False},
        )}},
        "pkg.unused": {"ignored": {"kind": "function", "may_park": True}},
        "pkg.main": {"call": {"kind": "function"}},
    }
    projected = a.closed_world_vthread_effect_export_surface(native, compact_redundant_names=True)
    legacy = tmp_path / "legacy.json"
    encoded = tmp_path / "effect.indexed"
    pipeline_exports._write_native_exports_wire(str(legacy), projected, {})
    wire.write_effect_exports(str(encoded), projected)
    full, _ = wire.read_effect_exports(str(legacy))
    module = parse_and_lift(
        "from pkg.leaf import park\nfrom pkg.owner import Worker\n"
        "def call():\n    park()\n    Worker().run()\n    Worker().quiet()\n",
        "case.py", "pkg.main",
    )
    expected = a.build_closed_world_vthread_effect_summary(module, "pkg.main", full)
    original = wire.json.loads
    calls = []

    def observe(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(wire.json, "loads", observe)
    lazy, derived = wire.read_effect_exports(str(encoded))
    assert derived == {} and tuple(lazy) == tuple(projected)
    assert "pkg.unused" in lazy and not calls
    assert a.build_closed_world_vthread_effect_summary(module, "pkg.main", lazy) == expected
    assert not isinstance(lazy["pkg.unused"], dict)
    assert all(isinstance(lazy[name], dict) for name in ("pkg.leaf", "pkg.owner", "pkg.main"))
    assert len(calls) == 3
    assert a.build_closed_world_vthread_effect_summary(module, "pkg.main", lazy) == expected
    assert len(calls) == 3
    assert wire.effect_module_exports(full, "pkg.leaf") is full["pkg.leaf"]


def test_effect_row_inventory_and_selected_json_validation(tmp_path):
    from pcc.frontends.python import vthread_effect_summary_wire as wire

    path = tmp_path / "effect.indexed"
    path.write_text(wire.EFFECT_EXPORT_SCHEMA + "\nvalid\t{}\ninvalid\t{\n")
    view, _ = wire.read_effect_exports(str(path))
    assert tuple(view) == ("valid", "invalid")
    assert wire.effect_module_exports(view, "valid") == {}
    with pytest.raises(ValueError, match="invalid vthread effect export module payload"):
        wire.effect_module_exports(view, "invalid")
    path.write_text(wire.EFFECT_EXPORT_SCHEMA + "\nvalid\t{}\nvalid\t{}\n")
    with pytest.raises(ValueError, match="duplicate vthread effect export module"):
        wire.read_effect_exports(str(path))
    path.write_text(wire.EFFECT_EXPORT_SCHEMA + "\nmissing-delimiter\n")
    with pytest.raises(ValueError, match="invalid vthread effect export row"):
        wire.read_effect_exports(str(path))


def test_effect_export_writer_preserves_destination_on_metadata_error(tmp_path):
    from pcc.frontends.python import vthread_effect_summary_wire as wire

    path = tmp_path / "effect.indexed"
    path.write_bytes(b"prior-complete-export")
    with pytest.raises(ValueError, match="invalid vthread effect export module table"):
        wire.write_effect_exports(str(path), {"valid": {}, "invalid": []})
    assert path.read_bytes() == b"prior-complete-export"


@pytest.mark.parametrize("hook", ["none", "reader", "builder"])
def test_summary_worker_facade_selects_lazy_reader_only_for_owned_analysis(monkeypatch, hook):
    captured = {}

    def run(manifest, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(p._pipeline_frontend_worker_execution, "run_summary_worker", run)
    if hook == "reader":
        monkeypatch.setattr(p, "_read_native_exports_wire", lambda path: ({}, {}))
    if hook == "builder":
        monkeypatch.setattr(p, "_build_closed_world_vthread_effect_summary", lambda *args: {})
    assert p._run_python_multi_summary_worker({}) == 0
    if hook == "none":
        assert captured["read_native_exports_wire"] is p._vthread_effect_summary_wire.read_effect_exports
    else:
        assert captured["read_native_exports_wire"] is p._read_native_exports_wire

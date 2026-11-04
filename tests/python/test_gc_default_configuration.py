"""Execute default configuration and first-freeze bodies at their cold race."""
import ast
import os
from pathlib import Path
import threading

import pytest

from test_gc_backend_configuration_race import FreezeRace

SOURCE = Path(os.environ.get('PCC_CONFIG_SOURCE', str(Path(__file__).resolve().parents[2] / 'pcc/runtime/py/freestanding_gc_public_collection.py')))


class ConfigRace(FreezeRace):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.words['pcc_gc_config_initialized'] = 0
        self.words['pcc_gc_backend_frozen'] = 0
        self.parser_entered = threading.Event()
        self.finish_config = threading.Event()
        self.first_freeze_or_wait = threading.Event()
        self.env_names = []
        self.second_reads = 0
        self.owner_result = None
        self.ns.update(i64=int, cstr=lambda text:text,
                       pcc_platform_getenv=self.getenv,
                       _pcc_gc_config_parse_backend=self.parse_backend,
                       pcc_gc_config_parse_env_i32=lambda value,default,lo,hi:default,
                       pcc_gc_refresh_read_barrier=lambda:None,
                       pcc_py_gc_minor_graph_lock=self.lock, pcc_py_gc_minor_graph_unlock=self.unlock,
                       pcc_gc_maybe_start_cms_worker=lambda:None,
                       pcc_thread_enable_conservative_roots=lambda:self.events.append('conservative'),
                       atomic_cas_i32=self.cas,
                       _init_config=lambda:self.ns['pcc_gc_config_ensure']())
        tree=ast.parse(SOURCE.read_text(),filename=str(SOURCE))
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='pcc_gc_config_ensure')
        function.decorator_list=[]
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def lock(self):
        if threading.current_thread().name=='allocation-freezer':
            self.first_freeze_or_wait.set()
        super().lock()

    def getenv(self, name):
        self.env_names.append(name)
        return str(self.backend) if name=='PCC_GC_BACKEND' else None

    def parse_backend(self, value, default):
        self.parser_entered.set()
        assert self.finish_config.wait(2)
        return int(value)

    def load(self, base, offset=0, *order):
        result=super().load(base,offset,*order)
        if threading.current_thread().name=='allocation-freezer':
            if base=='pcc_gc_config_initialized':
                self.second_reads+=1
                if self.second_reads>1 and result==0:
                    self.first_freeze_or_wait.set()
                    self.finish_config.wait(0.05)
            elif base=='pcc_gc_config_initializing' and result!=0:
                self.first_freeze_or_wait.set()
        return result

    def store(self, base, offset, value, *order):
        super().store(base,offset,value,*order)
        if base=='pcc_gc_backend_frozen' and value==1:
            self.first_freeze_or_wait.set()

    def configure(self):
        try:self.owner_result=self.ns['pcc_gc_config_ensure']()
        except BaseException as exc:self.failure.append(exc)

    def freeze_first_allocation(self):
        try:self.ns['pcc_gc_fix_backend']()
        except BaseException as exc:self.failure.append(exc)


@pytest.mark.parametrize('backend',range(5))
def test_first_freeze_waits_for_complete_default_configuration(backend):
    model=ConfigRace(backend)
    owner=threading.Thread(target=model.configure,name='config-owner')
    owner.start()
    assert model.parser_entered.wait(2)
    allocator=threading.Thread(target=model.freeze_first_allocation,name='allocation-freezer')
    allocator.start()
    try:
        assert model.first_freeze_or_wait.wait(2)
        early_frozen=model.frozen_backend
    finally:
        model.finish_config.set()
        allocator.join(timeout=2);owner.join(timeout=2)
    assert not allocator.is_alive() and not owner.is_alive() and not model.failure
    assert early_frozen is None, 'first allocation froze a partly initialized collector'
    assert model.owner_result==backend==model.frozen_backend==model.words['pcc_gc_backend_selected']
    assert model.words['pcc_gc_backend4_no_refcounts']==int(backend!=0)
    assert 'PCC_GC_TRACING_ONLY' not in model.env_names
    assert 'PCC_GC4_TRACING_ONLY' not in model.env_names

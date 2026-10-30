"""Actual graph-lock and thread-kernel bodies suppress lock-held polls."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT=Path(__file__).parents[2]/'pcc/runtime/py'

# pcc_tls_thread_state_py: no-park depth (bits 0-23), graph-lock depth
# (bits 24-47), scheduler lock held (48), registered (49).
NO_PARK_MASK=(1<<24)-1
GRAPH_UNIT=1<<24
SCHEDULER_HELD=1<<48
REGISTERED=1<<49


def no_park_depth(state):
    return state['pcc_tls_thread_state_py']&NO_PARK_MASK


def graph_depth(state):
    return (state['pcc_tls_thread_state_py']>>24)&NO_PARK_MASK


class SuspensionPathReached(Exception):
    pass


class LockTripwire(Exception):
    pass


class GraphPollModel:
    def __init__(self,stop_requested):
        self.state={'g_pcc_py_gc_minor_graph_lock':0,
                    # Thread 7 is registered and running.
                    'pcc_tls_thread_state_py':REGISTERED,
                    'pcc_tls_thread_id_py':7,
                    # A pending stop belongs to another thread.
                    'pcc_stop_owner_thread_id_py':8 if stop_requested else 0,
                    'pcc_thread_stop_requested':stop_requested}
        self.world_entries=[]
        self.env=dict(i64=int,c_ptr=int,
            global_addr=lambda name:name,
            load_i32=lambda address,offset:self.state[address],
            load_i64=lambda address,offset:self.state[address],
            store_i64=lambda address,offset,value:self.state.__setitem__(address,value),
            load_ptr=lambda address,offset:self.state[address],
            ptr_to_int=lambda value:value,
            atomic_load_i32=lambda address,offset,order:self.state[address],
            _world_i64=lambda address:self.state[address],
            store_i32=lambda address,offset,value:self.state.__setitem__(address,value),
            atomic_store_i32=lambda address,offset,value,order:self.state.__setitem__(address,value),
            atomic_cas_i32=self.compare_exchange,
            pcc_threads_enabled=lambda:1,pcc_current_thread_id=lambda:7,
            _tls_i64=lambda address:self.state[address],
            _finish_deferred_tripwire=lambda:None,
            _world_init=self.world_init,
            pcc_platform_abort=self.abort)
        self.load('freestanding_thread_kernel_pthread.py',{
            'pcc_thread_no_park_enter','pcc_thread_no_park_exit','pcc_thread_safepoint',
            'pcc_thread_graph_lock_acquire','pcc_thread_graph_lock_release',
        })
        self.env['thread_safepoint']=self.env['pcc_thread_safepoint']
        self.load('freestanding_runtime_high_substrate.py',{
            'pcc_py_gc_minor_graph_lock','pcc_py_gc_minor_graph_unlock',
        })

    def load(self,filename,names):
        path=ROOT/filename;tree=ast.parse(path.read_text())
        # The bodies read the module's graph-queue layout constants.
        for node in tree.body:
            if (isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name)
                    and node.target.id.startswith('_GRAPH_') and isinstance(node.value,ast.Constant)):
                self.env[node.target.id]=node.value.value
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes}==names
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.fix_missing_locations(ast.Module(nodes,[])),str(path),'exec'),self.env)

    def compare_exchange(self,address,offset,expected,desired,success,failure):
        old=self.state[address]
        if old==expected:self.state[address]=desired
        return old

    def world_init(self):
        self.world_entries.append(dict(self.state))
        raise SuspensionPathReached

    def abort(self):
        raise LockTripwire


@pytest.mark.parametrize('nested',(1,2))
@pytest.mark.parametrize('stop_requested',(0,1))
def test_actual_graph_transaction_suppresses_injected_thread_poll(nested,stop_requested):
    model=GraphPollModel(stop_requested)
    for _ in range(nested):model.env['pcc_py_gc_minor_graph_lock']()
    assert graph_depth(model.state)==nested
    assert model.state['g_pcc_py_gc_minor_graph_lock']==1
    assert no_park_depth(model.state)==1
    # This is the exact callee emitted at function/loop polling gates. Even
    # with a stop request, its no-park branch precedes initialization/parking.
    model.env['pcc_thread_safepoint']()
    assert model.world_entries==[]
    for _ in range(nested-1):model.env['pcc_py_gc_minor_graph_unlock']()
    assert graph_depth(model.state)==1
    # Releasing the last level polls: a pending stop takes the suspension
    # path only after the lock word, depth and no-park depth are released;
    # with nothing pending the poll returns without the world lock.
    if stop_requested:
        with pytest.raises(SuspensionPathReached):
            model.env['pcc_py_gc_minor_graph_unlock']()
        reached=model.world_entries[0]
    else:
        model.env['pcc_py_gc_minor_graph_unlock']()
        assert model.world_entries==[]
        reached=model.state
    assert reached['g_pcc_py_gc_minor_graph_lock']==0
    assert graph_depth(reached)==0
    assert no_park_depth(reached)==0


@pytest.mark.parametrize('stop_requested',(0,1))
def test_unleased_scheduler_lock_still_trips_before_suspension(stop_requested):
    model=GraphPollModel(stop_requested)
    model.state['pcc_tls_thread_state_py']|=SCHEDULER_HELD
    with pytest.raises(LockTripwire):model.env['pcc_thread_safepoint']()
    assert model.world_entries==[]

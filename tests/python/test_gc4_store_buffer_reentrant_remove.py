"""Reentrant buffered-value disposal must not invalidate queue traversal."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class FreedNodeAccess(Exception):
    pass


@pytest.mark.parametrize('queue', ['medium', 'global'])
@pytest.mark.parametrize('order', [(2, 1, 3), (1, 2, 3)])
def test_remove_finishes_queue_surgery_before_recursive_disposal(queue, order):
    # Owners A=100 and B=200. Removing A's first entry releases the last
    # buffered B owner; B's disposal recursively removes B's own entry.
    # B can be either the saved predecessor or the saved successor.
    fields = {1: [100, 1008, 200, 0], 2: [200, 2008, 400, 0],
              3: [100, 1016, 300, 0]}
    for position, node in enumerate(order):
        fields[node][3] = order[position+1] if position+1 < len(order) else 0
    heads = {'medium': order[0] if queue == 'medium' else 0,
             'global': order[0] if queue == 'global' else 0}
    dead = set()
    members = {100, 200}
    count = [3]
    medium_count = [3 if queue == 'medium' else 0]
    releases = []
    namespace = {}

    def load(node, offset):
        if node in dead:
            raise FreedNodeAccess(('read', node, offset))
        return fields[node][offset//8]

    def store(node, offset, value):
        if node in dead:
            raise FreedNodeAccess(('write', node, offset))
        fields[node][offset//8] = value

    def free(node):
        assert node not in dead
        dead.add(node)

    def drop(value):
        releases.append(value)
        if value == 200:
            namespace['_backend4_store_buffer_remove'](200)

    namespace.update(null=lambda:0, ptr_is_null=lambda x:x==0, is_tagged_int=lambda x:False,
        ptr_eq=lambda x,y:x==y, load_ptr=load, store_ptr=store,
        pcc_gc_store_buffer_member_maybe=lambda owner:int(owner in members),
        pcc_gc_store_buffer_member_forget=members.discard,
        pcc_gc_store_buffer_entry_forget=lambda owner,slot:None,
        _store_buffer_medium_head=lambda:heads['medium'],
        _store_buffer_head=lambda:heads['global'],
        _set_store_buffer_medium_head=lambda value:heads.__setitem__('medium',value),
        _set_store_buffer_head=lambda value:heads.__setitem__('global',value),
        _backend4_store_buffer_medium_count=lambda:medium_count[0],
        _backend4_store_buffer_medium_set_count=lambda value:medium_count.__setitem__(0,value),
        _backend4_store_buffer_dec=lambda:count.__setitem__(0,count[0]-1),
        py_decref=drop, free=free)
    path=ROOT/'pcc/runtime/py/py_gc_backend.py'
    node=next(n for n in ast.parse(path.read_text()).body
              if isinstance(n,ast.FunctionDef) and n.name=='_backend4_store_buffer_remove')
    node.decorator_list=[]
    exec(compile(ast.Module([node],type_ignores=[]),str(path),'exec'),namespace)
    namespace['_backend4_store_buffer_remove'](100)
    assert heads=={'medium':0,'global':0}
    assert dead=={1,2,3} and count[0]==medium_count[0]==0 and not members
    assert releases==[200,400,300]


SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_gc_generational_promote_young_if_known(void *);
extern int64_t pcc_gc_backend4_store_buffer_enqueue(void *, void *, void *);
extern void *pcc_gc_backend4_store_buffer_medium_head;
extern void *pcc_gc_backend4_store_buffer_head;
int main(void) {
    if (pcc_gc_set_backend(4) != 0) return 10;
    int32_t frame_map[1] = {4};
    PyObject *roots[4] = {0};
    pcc_gc_frame_enter(frame_map, roots);
    for (int i = 0; i < 4; ++i) {
        roots[i] = py_list_new(1);
        if (!roots[i]) return 11;
    }
    pcc_py_gc_minor_graph_lock();
    pcc_gc_generational_promote_young_if_known(roots[0]);
    /* A2 then A1. B is still young when A records its snapshot. */
    if (pcc_gc_backend4_store_buffer_enqueue(roots[0], 0, roots[2]) != 1) return 12;
    if (pcc_gc_backend4_store_buffer_enqueue(roots[0], 0, roots[1]) != 1) return 13;
    /* B can age while another owner's entry still keeps its snapshot. */
    pcc_gc_generational_promote_young_if_known(roots[1]);
    if (pcc_gc_backend4_store_buffer_enqueue(roots[1], 0, roots[3]) != 1) return 14;
    pcc_gc_store_root(&roots[1], 0);
    /* A removal now disposes B recursively. B is the saved predecessor. */
    pcc_gc_store_root(&roots[0], 0);
    if (pcc_gc_backend4_store_buffer_medium_head || pcc_gc_backend4_store_buffer_head) return 20;
    pcc_py_gc_minor_graph_unlock();
    if (py_list_len(roots[2]) != 0 || py_list_len(roots[3]) != 0) return 21;
    pcc_gc_store_root(&roots[3], 0);
    pcc_gc_store_root(&roots[2], 0);
    pcc_gc_frame_leave(roots);
    puts("GC4_BUFFER_REMOVE_OK");
    return 0;
}
'''


def test_native_reentrant_owner_removal_keeps_both_queues_valid(tmp_path, pcc_runtime_archive):
    import os
    import subprocess
    from tests.owned_c_execution import compile_owned_c_with_runtime

    source = tmp_path / 'buffer_remove.c'
    source.write_text(SOURCE)
    binary = tmp_path / 'buffer_remove'
    compile_owned_c_with_runtime(source, binary, runtime_archive=Path(pcc_runtime_archive))
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15,
                            env=dict(os.environ, PATH='/nonexistent'))
    assert (result.returncode, result.stdout, result.stderr) == (0, 'GC4_BUFFER_REMOVE_OK\n', '')

/* Two real boundary cuts, independent of the public facade value tests. */
#include "Python.h"
#include <stdint.h>
#include <stdio.h>

extern void pcc_gc_native_frame_enter_borrowed_slot(void *,void *,void *);
extern int64_t pcc_gc_native_frame_leave(void *);
extern PyObject *pcc_capi_pin_borrowed_result_gc4(PyObject *);
extern int64_t pcc_py_gc_minor_graph_lock_contended(void);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);

typedef struct State {
    int64_t ready, go;
    PyObject *from, *middle, *terminal;
    char mode;
} State;

static void *worker(void *arg) {
    State *s=(State *)arg;
    if (pcc_current_thread_id()<=0) return (void *)1;
    if (s->mode=='w') {
        /* Publish an actual stack slot while stopped-world code still has a
           legal source. Unlike conservative words, this slot must be rewritten. */
        int64_t frame[5]={0}, range[2]={0};
        PyObject *slot=s->from;
        pcc_thread_no_park_enter();
        pcc_gc_native_frame_enter_borrowed_slot(frame,range,&slot);
        pcc_thread_no_park_exit();
        __atomic_store_n(&s->ready,1,__ATOMIC_RELEASE);
        while (!__atomic_load_n(&s->go,__ATOMIC_ACQUIRE)) pcc_thread_safepoint();
        int ok=slot==s->terminal;
        if (pcc_gc_native_frame_leave(frame)!=0 || !ok) return (void *)2;
    } else {
        __atomic_store_n(&s->ready,1,__ATOMIC_RELEASE);
        while (!__atomic_load_n(&s->go,__ATOMIC_ACQUIRE)) pcc_thread_safepoint();
        /* This actual no-poll adapter cannot acquire the graph until the
           main thread publishes its second forwarding edge and unlocks. */
        PyObject *result=pcc_capi_pin_borrowed_result_gc4(s->from);
        if (result!=s->terminal || PyList_Size(result)!=0) return (void *)3;
    }
    return 0;
}

int main(int argc,char **argv){
    if (argc!=2 || argv[1][1] || (argv[1][0]!='w' && argv[1][0]!='c')) return 1;
    if (pcc_gc_set_backend(4) || pcc_gc_backend()!=4) return 2;
    py_gc_disable();
    State s={0}; s.mode=argv[1][0];s.from=PyList_New(0);s.middle=PyList_New(0);s.terminal=PyList_New(0);
    PyObject *spare=PyList_New(0), *owner=PyList_New(0);
    if (!s.from || !s.middle || !s.terminal || !owner || !spare) return 3;
    pcc_gc_pin(owner);
    if (PyList_Append(owner,s.from) || PyList_Append(owner,s.middle) || PyList_Append(owner,s.terminal)) return 4;
    void *thread=0,*status=0;
    if (pcc_thread_start(&thread,worker,&s) || !thread) return 5;
    while (!__atomic_load_n(&s.ready,__ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (s.mode=='w') {
        if (pcc_stop_the_world()!=0) return 6;
        if (pcc_gc_install_forwarding(s.from,s.middle)!=0 || pcc_gc_install_forwarding(s.middle,s.terminal)!=0) return 7;
        /* Normal outer resume runs the real remap-and-retire cut. Do not
           dereference an old shell after this point. */
        if (pcc_resume_world()!=0) return 8;
        __atomic_store_n(&s.go,1,__ATOMIC_RELEASE);
    } else {
        pcc_py_gc_minor_graph_lock();
        if (pcc_gc_install_forwarding(s.from,s.middle)!=0) return 9;
        __atomic_store_n(&s.go,1,__ATOMIC_RELEASE);
        int64_t deadline=pcc_runtime_monotonic_us()+2000000;
        while (!pcc_py_gc_minor_graph_lock_contended() && pcc_runtime_monotonic_us()<deadline) {}
        if (!pcc_py_gc_minor_graph_lock_contended()) return 10;
        if (pcc_gc_install_forwarding(s.middle,s.terminal)!=0) return 11;
        pcc_py_gc_minor_graph_unlock();
    }
    if (pcc_thread_join(thread,&status)!=0 || status) return 12;
    if (s.mode=='c' && pcc_gc_install_forwarding(s.terminal,spare)!=-2) return 13;
    printf("HANDOFF_OK %c\n",s.mode);
    return 0;
}

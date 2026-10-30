#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern PyObjectHeader py_none_storage, py_true_storage, py_false_storage;
extern PyObjectHeader py_notimplemented_storage, py_ellipsis_storage;
/* The pin balance is summed from the runtime's statistics shards. */
extern int64_t pcc_gc_metric_pin_balance(void);
extern void pcc_gc_immortalize(PyObject *);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
static PyObject *values[5];
static PyObject *permanent;
static void *worker(void *unused) {
    if(pcc_current_thread_id()<=0)return (void *)1;
    for(int repeat=0;repeat<5000;repeat++)for(int i=0;i<5;i++){
        pcc_gc_pin(values[i]);pcc_gc_unpin(values[i]);
    }
    return 0;
}
int main(int argc, char **argv) {
    if(argc!=2)return 1;
    int backend=atoi(argv[1]);
    values[0]=(PyObject *)&py_none_storage; values[1]=(PyObject *)&py_true_storage;
    values[2]=(PyObject *)&py_false_storage;values[3]=(PyObject *)&py_notimplemented_storage;
    values[4]=(PyObject *)&py_ellipsis_storage;
    /* The collector is selected before the first managed object and is
       fixed from then on: one process per collector. */
    {
        if(pcc_gc_set_backend(backend)!=0)return 10;
        for(int i=0;i<5;i++) {
            int64_t count = pcc_gc_metric_pin_balance();
            uint32_t flags=((PyObjectHeader *)values[i])->flags;
            int64_t refs=((PyObjectHeader *)values[i])->refcount;
            pcc_gc_pin(values[i]);
            if(pcc_gc_metric_pin_balance()!=count)return 20;
            if(((PyObjectHeader *)values[i])->flags!=flags)return 21;
            pcc_gc_unpin(values[i]);
            if(pcc_gc_metric_pin_balance()!=count || ((PyObjectHeader *)values[i])->flags!=flags)return 22;
            if(((PyObjectHeader *)values[i])->refcount!=refs)return 23;
        }
        /* Same IMMORTAL bit on a heap object is deliberately NOT admitted. */
        PyObject *heap=(PyObject *)py_list_new(0);if(!heap)return 24;
        permanent=heap;
        int64_t count = pcc_gc_metric_pin_balance();
        pcc_gc_pin(heap);if(pcc_gc_metric_pin_balance()!=count+1)return 25;
        if((((PyObjectHeader *)heap)->flags&64)==0)return 26;
        pcc_gc_unpin(heap);if(pcc_gc_metric_pin_balance()!=count)return 27;
        pcc_gc_immortalize(heap);if(pcc_gc_metric_pin_balance()!=count+1)return 28;
        pcc_gc_pin(heap);if(pcc_gc_metric_pin_balance()!=count+2)return 29;
        pcc_gc_unpin(heap);if(pcc_gc_metric_pin_balance()!=count+1)return 30;
        if((((PyObjectHeader *)heap)->flags&1)==0)return 31;
    }
    if(pcc_gc_set_backend((backend+1)%5)!=-1 || pcc_gc_backend()!=backend)return 32;
    int64_t count = pcc_gc_metric_pin_balance();
    void *threads[4];
    for(int i=0;i<4;i++)if(pcc_thread_start(&threads[i],worker,0)!=0)return 33;
    for(int i=0;i<4;i++){void *status=0;if(pcc_thread_join(threads[i],&status)!=0 || status)return 34;}
    if(pcc_gc_metric_pin_balance()!=count)return 35;
    puts("INTRINSIC_SINGLETON_PINS_OK");return 0;
}

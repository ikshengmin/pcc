/* These fixtures model legacy zpages, not allocator slabs. No allocator
 * spans exist; calls requiring a slab or conservative stack capture are bugs.
 * Header marks follow the production non-slab white-bit definition. */
int64_t pcc_gc_object_is_white(void *obj, int64_t flags){(void)obj;return (flags&8)!=0;}
void *pcc_allocator_record_span_first(void){return NULL;}
void *pcc_allocator_record_span_next(void *span){(void)span;abort();}
void *pcc_allocator_record_span_records(void *span){(void)span;abort();}
int64_t pcc_allocator_record_span_cells(void *span){(void)span;abort();}
void *pcc_allocator_record_span_page(void *span){(void)span;abort();}
int64_t pcc_gc_page_evacuating(void *page){(void)page;abort();}
void pcc_gc_page_set_evacuating(void *page,int64_t value){(void)page;(void)value;abort();}
void pcc_gc_pin_conservative_thread_roots(void){abort();}
int64_t pcc_gc_alloc_stage_drain_locked(void){return 0;}

extern void *py_subs_object_root(void);
extern void *py_class_attrs_dict(void *, int);
extern long pcc_gc_pointer_is_managed(void *);

long make_immortal_root_cache(void) {
    void *root = py_subs_object_root();
    if (!root) return -1;
    void *attrs = py_class_attrs_dict(root, 1);
    if (!attrs) return -2;
    return pcc_gc_pointer_is_managed(attrs);
}

long clear_immortal_cache_old_stack(void) {
    volatile long words[4096];
    for (int i = 0; i < 4096; ++i) words[i] = 0;
    return words[0];
}

long immortal_root_cache_is_live(void) {
    void *root = py_subs_object_root();
    if (!root) return -1;
    /* Query the authoritative slot's address without reading the child. */
    void *attrs = *(void **)((char *)root + @ATTRS@);
    if (!attrs) return -2;
    return pcc_gc_pointer_is_managed(attrs);
}

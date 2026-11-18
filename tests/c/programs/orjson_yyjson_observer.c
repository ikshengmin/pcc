/* External observer of the checked-in yyjson C ABI; the library is unmodified. */
#include "yyjson.h"
static int read_document(const yyjson_alc *alloc) {
    char input[] = "{\"items\":[17,25],\"truth\":true}";
    char bad[] = "[17,]";
    yyjson_read_err e = {0};
    yyjson_doc *doc = yyjson_read_opts(input, sizeof(input)-1, alloc, &e);
    if (!doc || e.code) return 11;
    yyjson_val *root = yyjson_doc_get_root(doc);
    yyjson_val *arr = yyjson_obj_get(root, "items");
    if (!yyjson_is_obj(root) || !yyjson_is_arr(arr) || yyjson_arr_size(arr)!=2) return 12;
    if (!yyjson_is_true(yyjson_obj_get(root,"truth")) || yyjson_arr_get(arr,2)) return 13;
    if (yyjson_get_uint(yyjson_arr_get(arr,0))+yyjson_get_uint(yyjson_arr_get(arr,1)) != 42) return 14;
    yyjson_doc_free(doc);
    doc = yyjson_read_opts(bad, sizeof(bad)-1, alloc, &e);
    if (doc || !e.code || !e.msg || !e.pos) return 15;
    return 0;
}
int main(int argc, char **argv, char **envp) {
    if (argc != 1 || !argv || !argv[0] || !envp) return 17;
    uint64_t storage[512];
    yyjson_alc pool;
    if (!yyjson_alc_pool_init(&pool,storage,sizeof(storage))) return 16;
    int rc=read_document(&pool);
    if (rc) return rc;
    rc=read_document((const yyjson_alc *)0);
    return rc ? rc : 42;
}

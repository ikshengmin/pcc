/* Original authored input for the common slot API, not a major-mark fix.
 * No managed object is fabricated: these raw lists are only enumerated by
 * the slot API with our address-counting callback, never passed to tracing.
 * The five backend configurations share this API; this is not lifecycle,
 * concurrent mutation, relocation, pause or throughput qualification. */
typedef long long i64;
struct List {
    i64 refcount;
    int tag;
    int flags;
    i64 length;
    i64 capacity;
    void **items;
};

extern i64 pcc_gc_config_ensure(void);
extern i64 pcc_gc_visit_object_slots_slice(
    void *, i64, i64, void (*)(void *, i64, void *), void *, void *);
extern i64 pcc_gc_visit_object_slots(
    void *, void (*)(void *, i64, void *), void *);
extern int puts(const char *);

static int payload[11];
static void *items[11];
static i64 calls;
static i64 start;
static int bad_slot;

static void observe(void *slot, i64 role, void *context) {
    if (context != items || role != 1 || calls >= 11 || start + calls >= 11 ||
        slot != &items[start + calls]) {
        bad_slot = 1;
    }
    calls += 1;
}

int main(int argc, char **argv) {
    struct List list;
    i64 state[2];
    i64 cursor;
    i64 total;
    i64 expected;
    int i;
    if (argc != 2 || argv[1][0] < '0' || argv[1][0] > '4' || argv[1][1])
        return 10;
    if (pcc_gc_config_ensure() != argv[1][0] - '0')
        return 11;
    /* The original fixture is an explicit Linux x86_64 ABI obligation. */
    if (sizeof(void *) != 8 || sizeof(i64) != 8 || sizeof(list) != 40 ||
        (char *)&list.tag - (char *)&list != 8 ||
        (char *)&list.length - (char *)&list != 16 ||
        (char *)&list.items - (char *)&list != 32)
        return 12;
    for (i = 0; i < 11; i += 1)
        items[i] = &payload[i];
    list.refcount = 0;
    list.tag = 5; /* PY_TYPE_LIST; verified against generated ABI in host test. */
    list.flags = 16;
    list.length = 11;
    list.capacity = 11;
    list.items = items;
    cursor = 0;
    total = 0;
    while (cursor >= 0) {
        start = cursor;
        calls = 0;
        state[0] = 999;
        state[1] = 999;
        expected = 11 - cursor;
        if (expected > 4)
            expected = 4;
        if (pcc_gc_visit_object_slots_slice(
                &list, cursor, 4, observe, items, state) != 1)
            return 20;
        if (bad_slot || state[1] != expected || calls != expected)
            return 21;
        total += state[1];
        if (state[0] != (total < 11 ? total : -1))
            return 22;
        cursor = state[0];
    }
    if (total != 11 || list.flags != 16)
        return 23;
    start = 0;
    calls = 0;
    if (pcc_gc_visit_object_slots(&list, observe, items) != 1 ||
        bad_slot || calls != 11 || list.flags != 16)
        return 24;
    puts("gc-slot-budget-contract-ok");
    return 0;
}

// Original C++17 source: a standard-layout stack record, not a shared GC object.
// On the two selected LP64 targets these fields are i32/u64/i64. The independent
// observer reads offsets 0/8/16 without reading the unspecified padding bytes.
struct AbiRecord {
    int tag;
    unsigned long long wide;
    long long signed_value;
};

extern "C" unsigned long long rust_read_record(const AbiRecord*);
extern "C" void record_snapshot(const AbiRecord*);

extern "C" unsigned long long record_entry() {
    AbiRecord record = {
        -123456789,
        0xfedcba9876543210ULL,
        -72623859790382856LL
    };
    record_snapshot(&record);
    unsigned long long value = rust_read_record(&record);
    record_snapshot(&record);
    return value;
}

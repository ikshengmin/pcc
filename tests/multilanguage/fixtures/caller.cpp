// Authored fixed input. Names are the current frontend export encodings;
// the harness verifies them, never rewrites this translation unit or its IR.
extern "C" int pcc_java_496e7465726f70_6d6978_2849494949494949492949(
    int, int, int, int, int, int, int, int);
extern "C" long long __pcc_go_main_466f6c64(
    long long, long long, long long, long long,
    long long, long long, long long, long long);
extern "C" unsigned int __pcc_rust_mix32(unsigned int, unsigned int);
extern "C" unsigned long long __pcc_rust_mix64(
    unsigned long long, unsigned long long, unsigned long long, unsigned long long,
    unsigned long long, unsigned long long, unsigned long long, unsigned long long);

extern "C" int observe_java() {
    return pcc_java_496e7465726f70_6d6978_2849494949494949492949(
        -2000000000, 1, 2, 4, 8, 16, 32, 64);
}

extern "C" long long observe_go() {
    return __pcc_go_main_466f6c64(-5000000000000LL, 1LL, 2LL, 4LL,
                                8LL, 16LL, 0x100000020LL, 0x200000040LL);
}

extern "C" unsigned int observe_rust32() {
    return __pcc_rust_mix32(0xf1234500U, 0x100U);
}

extern "C" unsigned long long observe_rust64() {
    return __pcc_rust_mix64(0xf123456789abc000ULL, 1ULL, 2ULL, 4ULL,
                          8ULL, 16ULL, 0x100000020ULL, 0x200000040ULL);
}

extern "C" long long observe_chain() {
    int java_value = observe_java();
    unsigned int rust_value = __pcc_rust_mix32(
        static_cast<unsigned int>(java_value), 0x100U);
    return __pcc_go_main_466f6c64(static_cast<long long>(rust_value),
                                1LL, 2LL, 4LL, 8LL, 16LL,
                                0x100000020LL, 0x200000040LL);
}

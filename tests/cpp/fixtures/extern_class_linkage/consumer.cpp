namespace fixture {
struct Record { int value; };
extern const Record value;
}
extern "C" const void* provider_address();
extern "C" const void* peer_address();
extern "C" int provider_value();
int cpp_test_entry() {
    const void* address = &fixture::value;
    if (address != provider_address()) return 1;
    if (address != peer_address()) return 2;
    return provider_value();
}

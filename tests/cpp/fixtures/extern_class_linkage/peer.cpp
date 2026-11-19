namespace fixture {
struct Record { int value; };
extern const Record value;
}
extern "C" const void* peer_address() { return &fixture::value; }

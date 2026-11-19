// External G++ provider only. This does not certify PCC class definitions.
namespace fixture {
struct Record { int value; };
extern const Record value = {42};
}
extern "C" const void* provider_address() { return &fixture::value; }
extern "C" int provider_value() { return fixture::value.value; }

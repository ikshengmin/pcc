// A second genuine external definition, not a PCC global-definition claim.
namespace fixture {
struct Record { int value; };
extern const Record value = {17};
}
extern "C" const void* provider_address() { return &fixture::value; }
extern "C" int provider_value() { return fixture::value.value; }

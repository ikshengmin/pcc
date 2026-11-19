namespace fixture {
struct Unused {
    Unused();
    ~Unused();
    int value;
};
extern const Unused unused;
}
int cpp_test_entry() { return 42; }

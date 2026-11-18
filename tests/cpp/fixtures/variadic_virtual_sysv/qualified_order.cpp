// C++17 evaluates a member call's postfix expression before its arguments.
int trace = 0;
struct Base {
    Base() {}
    virtual int call(int n) { return n; }
    static int direct(int n) { return n; }
};
struct Derived : Base {
    Derived() : Base() {}
    int call(int n) override { return 1; }
};
Derived* receiver(Derived* p) { trace = trace*10+1; return p; }
int argument() { trace = trace*10+2; return 42; }
int cpp_test_entry() {
    Derived d;
    if (receiver(&d)->Base::call(argument()) != 42 || trace != 12) return 1;
    trace = 0;
    if ((*receiver(&d)).Base::direct(argument()) != 42 || trace != 12) return 2;
    return 42;
}

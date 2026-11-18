struct R{int value;R(int);R(const R&);};
R::R(int n):value(n){}R::R(const R& r):value(r.value){}
R make(){R local(42);return local;}
int cpp_test_entry(){R r=make();return r.value;}

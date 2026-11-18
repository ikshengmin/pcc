int destroyed=0;
struct R{int value;R(int);R(const R&);~R();int get();};
R::R(int n):value(n){}R::R(const R& r):value(r.value){}
R::~R(){destroyed+=1;}int R::get(){return value;}
R make(){return R(42);}
int cpp_test_entry(){int result=make().get();if(destroyed!=1)return 1;return result;}

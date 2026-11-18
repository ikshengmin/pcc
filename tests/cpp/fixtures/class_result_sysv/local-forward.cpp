int constructed=0;int copied=0;
struct R{int value;R(int);R(const R&);};
R::R(int n):value(n){constructed+=1;}
R::R(const R& r):value(r.value){copied+=1;}
R make(){return R(42);}
R forward(){return make();}
int cpp_test_entry(){R r=forward();if(constructed!=1||copied!=0)return 1;return r.value;}

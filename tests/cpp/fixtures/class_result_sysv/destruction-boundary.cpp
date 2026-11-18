int made=0;int copied=0;int destroyed=0;int total=0;
struct R{int value;R(int);R(const R&);~R();};
R::R(int n):value(n){made+=1;}
R::R(const R& r):value(r.value){copied+=1;}
R::~R(){destroyed+=1;total+=value;}
R make(int n){return R(n);}
R forward(int n){return make(n);}
int cpp_test_entry(){
 {R r=forward(19);if(made!=1||copied!=0||destroyed!=0)return 1;}
 if(destroyed!=1||total!=19)return 2;
 make(23);
 if(made!=2||copied!=0||destroyed!=2||total!=42)return 3;
 return 42;
}

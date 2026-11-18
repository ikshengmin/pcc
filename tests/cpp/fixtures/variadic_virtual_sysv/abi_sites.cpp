#include <cstdarg>
struct V {
    V(){}
    virtual int call(int count,...) {
        va_list ap;va_start(ap,count);int result=0;
        for(int i=0;i<count;i++)result+=(int)va_arg(ap,double);
        va_end(ap);return result;
    }
};
int zero(V*p){return p->call(0);}
int integers(V*p){return p->call(0,10,20,30);}
int pair(V*p){return p->call(2,1.25f,2.5);}
int many(V*p){return p->call(9,1.,2.,3.,4.,5.,6.,7.,8.,9.);}
int cpp_test_entry(){V v;if(zero(&v)!=0||integers(&v)!=0||pair(&v)!=3||many(&v)!=45)return 1;return 42;}

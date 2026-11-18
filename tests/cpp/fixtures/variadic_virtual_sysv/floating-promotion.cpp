#include <cstdarg>
struct Base{virtual int sum(double fixed,int count,...);};
int Base::sum(double fixed,int count,...){va_list ap;va_start(ap,count);double n=fixed;
    for(int i=0;i<count;++i)n+=va_arg(ap,double);va_end(ap);return (int)n;}
struct Derived:Base{int sum(double fixed,int count,...)override;};
int Derived::sum(double fixed,int count,...){va_list ap;va_start(ap,count);double n=fixed+100;
    for(int i=0;i<count;++i)n+=va_arg(ap,double);va_end(ap);return (int)n;}
int cpp_test_entry(){Derived d;Base*p=&d;float a=1.5F;
    if(p->sum(0.5,0)!=100)return 1;
    if(p->sum(0.5,3,a,10.0,30.0)!=142)return 2;
    if(p->Base::sum(0.5,3,a,10.0,30.0)!=42)return 3;
    return 42;}

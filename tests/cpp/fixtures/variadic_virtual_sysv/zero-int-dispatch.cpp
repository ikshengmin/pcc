#include <cstdarg>
struct Base {
    virtual int sum(int count,...);
};
int Base::sum(int count,...){va_list ap;va_start(ap,count);int n=0;
    for(int i=0;i<count;++i)n+=va_arg(ap,int);va_end(ap);return n;}
struct Derived:Base{int sum(int count,...)override;};
int Derived::sum(int count,...){va_list ap;va_start(ap,count);int n=100;
    for(int i=0;i<count;++i)n+=va_arg(ap,int);va_end(ap);return n;}
Base* receiver(Base*p,int*n){*n=*n+1;return p;}
int tail(int*n){return *n==2?7:70;}
int cpp_test_entry(){Derived d;Base*p=&d;int calls=0;
    if(receiver(p,&calls)->sum(0)!=100||calls!=1)return 1;
    if(receiver(p,&calls)->sum(1,tail(&calls))!=107||calls!=2)return 4;
    if(p->sum(7,1,2,3,4,5,6,7)!=128)return 2;
    if(p->Base::sum(3,10,20,12)!=42)return 3;
    return 42;}

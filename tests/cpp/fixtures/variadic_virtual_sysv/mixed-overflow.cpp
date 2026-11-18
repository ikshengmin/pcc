#include <cstdarg>
struct Base{virtual int mixed(int count,...);};
int Base::mixed(int count,...){va_list ap;va_start(ap,count);int n=0;
    for(int i=0;i<count;++i){int k=va_arg(ap,int);double d=va_arg(ap,double);n+=k+(int)(d*2);}
    va_end(ap);return n;}
struct Derived:Base{int mixed(int count,...)override;};
int Derived::mixed(int count,...){va_list ap;va_start(ap,count);int n=100;
    for(int i=0;i<count;++i){int k=va_arg(ap,int);double d=va_arg(ap,double);n+=k+(int)(d*2);}
    va_end(ap);return n;}
Base* receiver(Base*p,int*n){*n=*n+1;return p;}
int cpp_test_entry(){Derived object;Base*p=&object;int count=0;
    int n=receiver(p,&count)->mixed(10,1,0.5,2,1.5,3,2.5,4,3.5,5,4.5,6,5.5,7,6.5,8,7.5,9,8.5,10,9.5);
    if(n!=255||count!=1)return 1;
    if(p->Base::mixed(2,10,1.5,20,4.5)!=42)return 2;
    return 42;}

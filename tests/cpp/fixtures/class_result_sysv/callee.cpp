namespace probe {
struct Handle {
    long value;
    int* copies;
    Handle(long value, int* copies);
    Handle(const Handle& other);
    Handle press(double scale, long a, long b, long c, long d, long e, long f, long g) const;
    Handle copy() const;
};
Handle make(int* copies, double scale, long a, long b, long c, long d, long e, long f, long g);
}

namespace probe {
Handle::Handle(long v,int* n):value(v),copies(n){}
Handle::Handle(const Handle& other):value(other.value),copies(other.copies){*copies=*copies+1;}
Handle make(int* n,double scale,long a,long b,long c,long d,long e,long f,long g){
    return Handle(a+10*b+100*c+1000*d+10000*e+100000*f+1000000*g+(long)(scale*10),n);
}
Handle Handle::press(double scale,long a,long b,long c,long d,long e,long f,long g)const{
    return Handle(value+a+10*b+100*c+1000*d+10000*e+100000*f+1000000*g+(long)(scale*10),copies);
}
Handle Handle::copy()const{return *this;}
}

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

int cpp_test_entry(){
    int copies=0;
    probe::Handle h=probe::make(&copies,2.5,1L,2L,3L,4L,5L,6L,7L);
    if(h.value!=7654346L||copies!=0)return 1;
    probe::Handle p=h.press(1.5,7L,6L,5L,4L,3L,2L,1L);
    if(p.value!=8888928L||copies!=0)return 2;
    probe::Handle c=h.copy();
    if(c.value!=7654346L||c.copies!=&copies||copies!=1)return 3;
    probe::Handle d=c.copy();
    if(d.value!=7654346L||copies!=2)return 4;
    return 42;
}

namespace abi {
struct Handle {
    void* p;
    Handle(void* q);
    Handle(const Handle& other);
    Handle copy(int delta) const;
};
Handle make(void* p);
}
int main(){int x=42;abi::Handle h=abi::make(&x);abi::Handle c=h.copy(7);
return h.p==&x&&c.p==&x?0:1;}

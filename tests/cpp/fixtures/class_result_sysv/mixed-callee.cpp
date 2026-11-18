namespace abi {
struct Handle {
    void* p;
    Handle(void* q);
    Handle(const Handle& other);
    Handle copy(int delta) const;
};
Handle make(void* p);
}
namespace abi {
Handle::Handle(void* q):p(q){}
Handle::Handle(const Handle& other):p(other.p){}
Handle Handle::copy(int delta) const { if(delta==7)return *this; return Handle(nullptr); }
Handle make(void* p){ return Handle(p); }
}

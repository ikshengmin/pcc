#include <cstdarg>
// A public emitter invokes a protected variadic override, each with a real
// in-TU body. The va_list is restarted on the same fixed format parameter.
struct Emitter {
    int seen;
    Emitter():seen(0){}
    void publish(int n, float fraction) { print("p", n, fraction); }
protected:
    virtual void print(const char* format, ...);
};
void Emitter::print(const char* format, ...){ seen=3; }
struct Recorder: Emitter {
    Recorder():Emitter(){}
protected:
    void print(const char* format, ...) override;
};
void Recorder::print(const char* format, ...) {
    va_list cursor;
    va_start(cursor, format); int a=va_arg(cursor,int); va_end(cursor);
    va_start(cursor, format); int b=va_arg(cursor,int); double d=va_arg(cursor,double); va_end(cursor);
    seen=format[0]=='p' ? a+b+(int)d : 1;
}
int cpp_test_entry(){Recorder r; Emitter* base=&r; base->publish(19,4.25f); return r.seen;}

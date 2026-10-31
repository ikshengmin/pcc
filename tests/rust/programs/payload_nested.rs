#![no_std]
enum Inner { Zero, Value(u64) }
enum Outer { Missing, Wrapped(Inner,bool) }
fn inspect(x:Outer)->u64 {
    match x {
        Outer::Missing=>3,
        Outer::Wrapped(Inner::Zero,_)=>5,
        Outer::Wrapped(Inner::Value(_),false)=>7,
        Outer::Wrapped(Inner::Value(value),true)=>value,
    }
}
pub extern "C" fn answer()->u64 {
    if inspect(Outer::Missing)!=3 || inspect(Outer::Wrapped(Inner::Zero,true))!=5 {return 0;}
    if inspect(Outer::Wrapped(Inner::Value(11),false))!=7{return 0;}
    inspect(Outer::Wrapped(Inner::Value(0xfedcba9876543210),true))
}

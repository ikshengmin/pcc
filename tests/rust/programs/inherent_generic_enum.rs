#![no_std]
enum Choice<T> {Empty,Value(T)}
impl<T> Choice<T> {
    fn wrap(value:T)->Self {Self::Value(value)}
    fn unwrap_or(self,fallback:T)->T {match self {Self::Empty=>fallback,Self::Value(value)=>value}}
}
struct Value {n:u64}
impl Value {fn into(self)->u64 {self.n}}
pub extern "C" fn answer()->u64 {
    let first=Choice::<Value>::wrap(Value{n:0xfedcba9876543210});
    let empty=Choice::<u64>::Empty;
    if empty.unwrap_or(7)!=7{return 0;}
    first.unwrap_or(Value{n:0}).into()
}

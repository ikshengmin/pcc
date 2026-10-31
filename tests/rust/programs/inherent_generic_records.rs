#![no_std]
struct Pair<A,B> {first:A,second:B}
impl<T,U> Pair<U,T> {
    fn new(first:U,second:T)->Self {Self{first:first,second:second}}
    fn second(self)->T {self.second}
}
struct Value {n:u64}
impl Value {fn into(self)->u64 {self.n}}
pub extern "C" fn answer()->u64 {
    Pair::<bool,Value>::new(true,Value{n:0xfedcba9876543210}).second().into()
}

trait Value { fn value(self) -> u64; fn make(x:u64) -> Self; }
struct R { x:u64 }
impl Value for R {
    fn value(self)->u64 { self.x }
    fn make(x:u64)->Self { Self{x:x} }
}
enum E { A(u64), B }
impl Value for E {
    fn value(self)->u64 { match self { E::A(x)=>x, E::B=>0 } }
    fn make(x:u64)->Self { E::A(x) }
}
pub fn answer()->u64 {
    let a=<R as Value>::make(17).value();
    let b=E::make(25).value();
    if a+b==42 { 0xfedcba9876543210u64 } else { 0 }
}

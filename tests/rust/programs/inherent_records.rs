#![no_std]
struct Value { n:u64 }
impl Value {
    fn make(n:u64)->Self {Self{n:n}}
    fn add(mut self,n:u64)->Self {self.n+=n;self}
    fn into(self)->u64 {self.n}
}
pub extern "C" fn answer()->u64 {
    Value::into(Value::make(0xfedcba9876543200).add(16))
}

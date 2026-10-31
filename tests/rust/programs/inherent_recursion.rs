#![no_std]
struct Value<T> {value:T}
impl<T> Value<T> {
    fn walk(self,n:i64)->T {if n==0 {self.value} else {self.other(n-1)}}
    fn other(self,n:i64)->T {self.walk(n)}
}
pub extern "C" fn answer()->u64 {
    Value::<u64>{value:0xfedcba9876543210}.walk(4)
}

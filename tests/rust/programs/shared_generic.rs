#![no_std]
struct Payload{x:u64}
struct Record<T>{value:T,n:u64}
impl<T> Record<T>{
    fn number(&self)->u64{self.n}
    fn into(self)->T{self.value}
}
fn number<T>(p:&Record<T>)->u64{p.number()}
pub extern "C" fn answer()->u64{
    let r=Record::<Payload>{value:Payload{x:16},n:0xfedcba9876543200};
    let n=number::<Payload>(&r);
    n+r.into().x
}

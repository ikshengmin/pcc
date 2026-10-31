#![no_std]
struct Value{n:u64}
impl Value{fn add(&self,a:u64,b:u64)->u64{self.n+a*10+b}}
pub extern "C" fn answer()->u64{
    let v=Value{n:0xfedcba9876543200};
    let mut count=0u64;
    let value=v.add({count+=1;count},{count+=1;count});
    if count!=2{return 0;}
    value+4
}

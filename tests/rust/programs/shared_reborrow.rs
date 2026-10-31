#![no_std]
struct Inner{n:u64}
struct Outer{inner:Inner,flag:bool}
impl Inner{fn read(&self)->u64{self.n}}
fn read(p:&Outer)->u64{
    if !p.flag{return 0;}
    p.inner.read()
}
pub extern "C" fn answer()->u64{
    let p=Outer{inner:Inner{n:0xfedcba9876543210},flag:true};
    read(&p)
}

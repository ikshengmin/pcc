#![no_std]
struct Inner{n:u64}
struct Outer{inner:Inner}
fn value(p:&Inner)->u64{(*p).n}
fn read(p:&Outer)->u64{value(&(*p).inner)}
pub extern "C" fn answer()->u64{
    let p=Outer{inner:Inner{n:0xfedcba9876543210}};
    read(&p)
}

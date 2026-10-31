#![no_std]
struct Value{n:u64}
fn add(p:&u64,n:u64)->u64{*p+n}
fn read(mut p:&Value,q:&Value)->u64{add(&p.n,{p=q;p.n})}
pub extern "C" fn answer()->u64{
    let a=Value{n:0xfedcba9876543200};
    let b=Value{n:16};
    read(&a,&b)
}

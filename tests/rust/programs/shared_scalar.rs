#![no_std]
fn read(p:&u64)->u64{*p}
pub extern "C" fn answer()->u64{
    let mut n=0xfedcba9876543200u64;
    let first=read(&n);
    n+=16;
    if first!=0xfedcba9876543200 {return 0;}
    read((&n))
}

#![no_std]
struct P { value:u64 }
fn take(p:P)->u64 {p.value}
pub extern "C" fn answer()->u64 {
    let pair=(P{value:0xfedcba9876543210},P{value:7});
    let first=match pair {(p,_)=>take(p)};
    let second=match pair {(_,p)=>take(p)};
    // Tuple wildcard projection does not inspect the already moved field.
    match pair {(_,_)=>()};
    if second!=7{return 0;}
    first
}

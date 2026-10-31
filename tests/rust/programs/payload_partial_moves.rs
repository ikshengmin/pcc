#![no_std]
struct P { value:u64 }
enum Pair { Empty, Both(P,P) }
fn take(p:P)->u64 {p.value}
pub extern "C" fn answer()->u64 {
    let x=Pair::Both(P{value:0xfedcba9876543210},P{value:7});
    let first=match x {Pair::Empty=>0,Pair::Both(p,_)=>take(p)};
    // Reading a tag does not read a moved payload. The other field remains live.
    let second=match x {Pair::Empty=>0,Pair::Both(_,p)=>take(p)};
    match x {Pair::Empty=>return 0,Pair::Both(_,_)=>()};
    if second!=7{return 0;}
    first
}

#![no_std]
struct P { value:u64 }
enum E { None, Item(P) }
fn take(p:P)->u64{p.value}
pub extern "C" fn answer()->u64 {
    let mut e=E::Item(P{value:0xfedcba9876543210});
    let mut i=0;
    while i<3 {
        match e {E::None=>(),E::Item(p)=>{e=E::Item(p);}};
        i+=1;
    }
    let outer=19;
    let increment=match (7i64,11i64){(mut outer,other)=>{outer+=other;outer}};
    if increment!=18 || outer!=19{return 0;}
    match e {E::None=>0,E::Item(p)=>take(p)}
}

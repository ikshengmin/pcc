#![no_std]
struct Value {n:u64}
impl Value {fn add(self,n:u64)->u64 {self.n+n}}
fn take(x:Value)->u64 {x.n}
pub extern "C" fn answer()->u64 {
    let mut n=0;
    let result=({n=n*10+1;Value{n:0xfedcba9876543200}}).add({n=n*10+2;16});
    if n!=12{return 0;}
    let x=Value{n:7};
    let y=Value{n:take(x)};
    if y.add(1)!=8{return 0;}
    result
}

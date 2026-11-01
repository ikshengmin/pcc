struct R{n:u64}
fn replace<T>(p:&mut T,value:T){*p=value;}
pub fn answer()->u64{let mut r=R{n:7};replace(&mut r,R{n:0xfedcba9876543210});r.n}

struct R{value:u64}
fn replace<'a>(mut p:&'a mut R,q:&'a mut R){p.value={p=q;0xfedcba9876543210};}
pub fn answer()->u64{let mut a=R{value:7};let mut b=R{value:9};replace(&mut a,&mut b);if a.value!=7{return 0;}b.value}

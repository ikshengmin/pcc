fn replace<'a>(mut p:&'a mut u64,q:&'a mut u64){*p={p=q;0xfedcba9876543210};}
pub fn answer()->u64{let mut a=7;let mut b=9;replace(&mut a,&mut b);if a!=7{return 0;}b}

fn replace<'a>(mut p:&'a mut[u64;2],q:&'a mut[u64;2]){p[1]={p=q;0xfedcba9876543210};}
pub fn answer()->u64{let mut a=[3,4];let mut b=[5,6];replace(&mut a,&mut b);if a[1]!=4{return 0;}b[1]}

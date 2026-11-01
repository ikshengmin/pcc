fn replace<'a>(mut p:&'a mut u64,q:&'a mut u64){p=q;*p+=2;}
pub fn answer()->u64{let mut a=7;let mut b=0xfedcba987654320e;replace(&mut a,&mut b);if a!=7{return 0;}b}

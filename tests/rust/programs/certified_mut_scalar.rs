fn bump(p:&mut u64){*p+=3;}
pub fn answer()->u64{let mut x=0xfedcba987654320d; bump(&mut x); x}

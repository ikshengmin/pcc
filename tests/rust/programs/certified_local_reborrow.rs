fn read(p:&u64)->u64{*p}
fn bump(p:&mut u64){let q=&mut *p;*q+=1;}
pub fn answer()->u64{let mut x=0xfedcba987654320f;let p=&mut x;bump(p);read(p)}

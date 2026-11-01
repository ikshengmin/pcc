fn replace<'a>(mut p:&'a u64,q:&'a u64)->u64{p=q;*p}
pub fn answer()->u64{let a=7;let b=0xfedcba9876543210;replace(&a,&b)}

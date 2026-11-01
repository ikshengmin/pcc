fn make(p:&mut u64)->[u64;2]{*p+=1;[1,2]}
pub fn answer()->u64{let mut c=0;let n=make(&mut c).len();if n!=2usize||c!=1{return 0;}0xfedcba9876543210}

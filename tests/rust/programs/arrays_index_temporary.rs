fn make(p:&mut u64)->[u64;1+1]{*p+=1;[0xfedcba9876543210,3]}
pub fn answer()->u64{let mut c=0;let value=make(&mut c)[1usize-1usize];if c!=1{return 0;}value}

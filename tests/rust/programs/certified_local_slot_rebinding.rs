pub fn answer()->u64{let mut a=7;let mut b=9;let mut p=&mut a;*p={p=&mut b;0xfedcba9876543210};if a!=7{return 0;}b}

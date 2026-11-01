fn echo(mut a:[u64;3])->[u64;3]{a[0]=9;a}
pub fn answer()->u64{let a=[0xfedcba9876543210u64,8,7];let b=echo(a);if b[0]!=9||a[0]!=0xfedcba9876543210{return 0;}a[0]}

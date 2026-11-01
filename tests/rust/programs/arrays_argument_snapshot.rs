fn take(a:[u64;3],n:u64)->u64{if n!=1{return 0;}a[0]}
fn change(a:&mut[u64;3])->u64{a[0]=9;1}
pub fn answer()->u64{let mut a=[0xfedcba9876543210,2,3];let value=take(a,change(&mut a));if a[0]!=9{return 0;}value}

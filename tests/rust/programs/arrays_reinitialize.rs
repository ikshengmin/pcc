struct R{n:u64}
fn take(a:[R;1])->u64{a[0].n}
pub fn answer()->u64{let mut a=[R{n:7}];let b=take(a);a=[R{n:0xfedcba9876543210}];if b!=7{return 0;}take(a)}

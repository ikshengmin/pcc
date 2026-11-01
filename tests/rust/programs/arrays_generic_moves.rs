struct R{n:u64}
fn identity<T>(a:[T;2])->[T;2]{a}
pub fn answer()->u64{let a=[R{n:0xfedcba9876543210},R{n:6}];let b=identity(a);if b[1].n!=6{return 0;}b[0].n}

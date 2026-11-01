struct R{n:u64}
fn make(p:&mut u64)->R{*p+=1;R{n:0xfedcba9876543210}}
pub fn answer()->u64{let mut c=0;let z:[R;0]=[make(&mut c);0];let a=[make(&mut c);1];if c!=2||z.len()!=0usize{return 0;}a[0].n}

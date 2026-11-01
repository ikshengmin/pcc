struct R{n:u64,step:u64}
fn change(p:&mut R){p.n+=p.step;}
pub fn answer()->u64{let mut r=R{n:0xfedcba987654320b,step:5}; change(&mut r); r.n}

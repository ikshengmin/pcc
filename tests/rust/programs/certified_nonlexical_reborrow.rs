struct R{n:u64}
fn read(r:&R)->u64{r.n}
pub fn answer()->u64{let mut r=R{n:1};let p=&r;let n=read(p);r.n=0xfedcba987654320f;let q=&mut r;q.n+=n;r.n}

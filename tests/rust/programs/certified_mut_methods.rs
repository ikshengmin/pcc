struct R{n:u64}
impl R{fn add(&mut self,n:u64){self.n+=n;} fn read(&self)->u64{self.n}}
pub fn answer()->u64{let mut r=R{n:1};r.add(r.read());r.add(0xfedcba987654320e);r.read()}

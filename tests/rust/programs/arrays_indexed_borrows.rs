struct R{n:u64}
impl R{fn read(&self)->u64{self.n}}
fn peek(p:&u64)->u64{*p}
pub fn answer()->u64{let a=[0xfedcba9876543210,2];let value=peek(&a[0]);let records=[R{n:value}];records[0].read()}

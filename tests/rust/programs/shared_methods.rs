#![no_std]
struct Value{n:u64}
impl Value{
    fn read(&self)->u64{self.n}
    fn finish(self)->u64{self.n}
}
fn forward(p:&Value)->u64{p.read()}
pub extern "C" fn answer()->u64{
    let mut v=Value{n:0xfedcba9876543200};
    if forward(&v)!=v.read(){return 0;}
    v.n+=16;
    if Value::read(&v)!=0xfedcba9876543210{return 0;}
    v.finish()
}

#![no_std]
struct Three {a:u64,b:u64,c:u64}
impl Three {
    fn make()->Self {Self{a:7,b:9,c:0xfedcba9876543210}}
    fn exchange(mut self)->Self {let old=self.a;self.a=self.b;self.b=old;self}
    fn finish(self)->u64 {if self.a!=9||self.b!=7{return 0;}self.c}
}
pub extern "C" fn answer()->u64 {Three::make().exchange().finish()}

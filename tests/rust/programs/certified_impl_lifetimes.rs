struct Total{sum:u64}
impl<'a> Total{fn add(&mut self,value:&'a u64){self.sum+=*value;} fn read(&self)->u64{self.sum}}
trait Absorb{fn absorb(&mut self,value:&u64);}
impl<'b> Absorb for Total{fn absorb(&mut self,value:&u64){self.sum+=*value;}}
pub fn answer()->u64{let mut t=Total{sum:0};let a=0xfedcba9876543200u64;let b=0x10u64;t.add(&a);t.absorb(&b);t.read()}

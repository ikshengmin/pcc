struct R<T>{value:T}
impl<T> R<T>{fn replace(&mut self,value:T){self.value=value;}}
fn read(p:&u64)->u64{*p}
pub fn answer()->u64{let mut r=R::<u64>{value:7};let p=&mut r;p.replace(0xfedcba9876543210);read(&mut p.value)}

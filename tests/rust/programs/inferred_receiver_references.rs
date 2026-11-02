struct R<T>{x:T}
impl<T> R<T>{
    fn choose<U>(&self,u:U)->U{u}
    fn set(&mut self,x:T){self.x=x;}
    fn get(self)->T{self.x}
}
fn make<T>(x:T)->R<T>{R::<T>{x:x}}
pub fn answer()->u64{
    let mut r=make(1);
    let shared=&r;
    let ok=shared.choose::<bool>(true);
    r.set(4294967296u64);
    let unique=&mut r;
    unique.set(0xfedcba9876543210u64);
    if ok{r.get()}else{0}
}

struct R<T>{x:T}
impl<T> R<T>{fn get(self)->T{self.x}}
fn make<T>(x:T)->R<T>{R::<T>{x:x}}
fn result()->u64{make(0xfedcba9876543210).get()}
pub fn answer()->u64{
    let later=make(4294967296).get();
    let wide:u64=later;
    let fallback=make(7).get();
    if wide==4294967296 && fallback as u64==7{result()}else{0}
}

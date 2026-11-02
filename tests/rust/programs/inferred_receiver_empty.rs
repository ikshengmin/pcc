struct R<T>{x:T}
impl<T> R<T>{fn get(self)->T{self.x}}
fn empty<T>()->R<[T;0]>{R::<[T;0]>{x:[]}}
fn take(value:[u64;0])->u64{value.len() as u64}
pub fn answer()->u64{
    let annotation:[u64;0]=empty().get();
    let later=empty().get();
    let result:[u64;0]=later;
    if take(annotation)+take(result)+take(empty().get())==0{
        0xfedcba9876543210u64
    }else{0}
}

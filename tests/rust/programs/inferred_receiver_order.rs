struct R<T>{x:T}
impl<T> R<T>{fn pair<U>(self,u:U)->(T,U){(self.x,u)}}
fn make<T>(x:T)->R<T>{R::<T>{x:x}}
pub fn answer()->u64{
    let mut order=0u64;
    let result:(u64,u64)=({order=order*10+1;make(4294967296)})
        .pair({order=order*10+2;order});
    if order==12 && result.0==4294967296 && result.1==12{
        0xfedcba9876543210u64
    }else{0}
}

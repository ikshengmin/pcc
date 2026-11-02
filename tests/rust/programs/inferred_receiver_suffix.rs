struct Pair<A,B>{a:A,b:B}
impl<X,Y> Pair<Y,X>{fn pack<U>(self,u:U)->(Y,X,U){(self.a,self.b,u)}}
fn pair<A,B>(a:A,b:B)->Pair<A,B>{Pair::<A,B>{a:a,b:b}}
struct R<T>{x:T}
impl<T> R<T>{
    fn map<U>(self,u:U)->R<U>{R::<U>{x:u}}
    fn get(self)->T{self.x}
}
fn make<T>(x:T)->R<T>{R::<T>{x:x}}
pub fn answer()->u64{
    let explicit:(u64,bool,u32)=pair(4294967296,true).pack::<u32>(9);
    let inferred:(u64,bool,u32)=pair(4294967296,false).pack(11);
    let chained=make(1).map(0xfedcba9876543210).get();
    if explicit.0==4294967296 && explicit.1 && explicit.2==9
        && inferred.0==4294967296 && !inferred.1 && inferred.2==11{chained}else{0}
}

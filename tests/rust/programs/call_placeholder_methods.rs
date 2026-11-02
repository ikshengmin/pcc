struct Pair<A,B>{a:A,b:B}
impl<X,Y> Pair<Y,X>{
    fn choose<U,V>(self,u:U,v:V)->(Y,U,V){(self.a,u,v)}
    fn empty<U>()->[U;0]{[]}
}
fn make<T>(value:T)->Pair<T,bool>{Pair::<T,bool>{a:value,b:true}}
pub fn answer()->u64{
    let selected=make(4294967296).choose::<_,bool>(9u32,true);
    let later:u64=selected.0;
    let direct=Pair::<u64,bool>::choose::<_,_>(make(0xfedcba9876543210),true,7u32);
    let empty:[bool;0]=Pair::<u64,bool>::empty::<_>();
    if later==4294967296u64 && selected.1==9u32 && selected.2 && direct.1 && direct.2==7u32 && empty.len()==0{
        direct.0
    }else{0}
}

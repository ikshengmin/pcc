struct Pair<A,B>{a:A,b:B}
impl<X,Y> Pair<Y,X>{
    fn package<U>(self,u:U)->(Y,X,U){(self.a,self.b,u)}
}
pub fn answer()->u64{
    let first=Pair::<u64,bool>{a:17,b:true}.package(9u32);
    let second=Pair::<u64,bool>::package::<u32>(Pair::<u64,bool>{a:23,b:false},11);
    if first.0==17 && first.1 && first.2==9 && second.0==23 && !second.1 && second.2==11{
        0xfedcba9876543210u64
    }else{0}
}

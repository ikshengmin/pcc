struct Pair<A,B>{a:A,b:B}
impl<X,Y>Pair<Y,X>{
    fn new<U>(a:Y,b:X,u:U)->(Self,U){(Self{a:a,b:b},u)}
    fn take<U>(self,u:U)->(Y,U){(self.a,u)}
}
struct Value{x:u64}
impl Value{fn take(self)->u64{self.x}}
fn make(x:u64)->Value{Value{x:x}}
pub fn answer()->u64{
    let data=Pair::<u64,bool>::new::<>(0xfedcba9876543210,true,7u32);
    let marker=data.1;
    let result=data.0.take::<>(true);
    let direct=Value::take::<>(make::<>(result.0));
    let dotted=make::<>(direct).take::<>();
    if result.1 && marker==7 {dotted}else{0}
}

struct R{x:u64} impl R{fn id<U>(x:U)->U{x} fn pass<R>(self)->Self{Self::id(self)}} fn f()->u64{R{x:42}.pass::<bool>().x}
struct Box<T>{x:T}
impl<T> Box<T>{
    fn echo<U>(self,x:U)->U{x}
    fn nested<Box>(self,x:Box)->Box{self.echo(x)}
    fn identity<U>(x:U)->U{x}
    fn preserve<Box>(self)->Self{Self::identity(self)}
}
struct W<T>{x:T}
impl<T> W<T>{
    fn id<U>(self,u:U)->Self{self}
    fn pass<R>(self,r:R)->Self{self.id::<R>(r)}
    fn unused<U>(self){}
}
fn fixed_prefix()->u64{W::<R>{x:R{x:42}}.pass::<bool>(true).x.x}
pub fn answer()->u64{
    let x=Box::<u64>{x:17}.nested(true);
    let y=Box::<u64>{x:42}.preserve::<bool>();
    W::<u64>{x:1}.unused::<bool>();
    if f()==42 && fixed_prefix()==42 && x && y.x==42{0xfedcba9876543210u64}else{0}
}

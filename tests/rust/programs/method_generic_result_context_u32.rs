enum Maybe<T>{None,Some(T)}
struct Factory{n:u64}
impl Factory{
    fn empty<U>()->[U;0]{[]}
    fn none<U>(self)->Maybe<U>{Maybe::<U>::None}
    fn head<U>(x:(U,))->U{x.0}
}
fn forever<V>()->V{forever()}
fn outer<V>(flag:bool,x:V)->V{if flag{Factory::head(forever())}else{x}}
pub fn answer()->u64{
    let mut a=Factory::empty();a=[0u32;0];
    let b:[u64;0]=Factory::empty::<u64>();
    let n:Maybe<u32>=Factory{n:1}.none();
    let value=match n{Maybe::<u32>::None=>42u64,Maybe::<u32>::Some(x)=>x as u64};
    if a.len()==0 && b.len()==0 && value==42 && outer(false,42u64)==42{
        0xfedcba9876543210u64
    }else{0}
}

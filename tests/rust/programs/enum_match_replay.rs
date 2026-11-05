enum E<T>{Some(T)}
enum Maybe<T>{Some(T),None}
fn wrap<U>(x:U)->E<U>{E::<_>::Some(x)}
fn identity<U>(x:U)->U{match wrap(x){E::Some(y)=>y}}
fn empty<U>()->Maybe<U>{Maybe::<_>::None}
fn zero()->u64{match empty(){Maybe::Some(x)=>x,Maybe::None=>0}}
pub fn answer()->u64{
    if identity(true) && identity(9u32)==9u32 && zero()==0{
        identity(0xfedcba9876543210)
    }else{0}
}

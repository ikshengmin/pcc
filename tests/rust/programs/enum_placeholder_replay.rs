enum Maybe<T>{None,Some(T)}
fn wrap<U>(value:U)->Maybe<U>{Maybe::<_>::Some(value)}
fn empty<U>()->Maybe<U>{Maybe::<_>::None}
fn outer<U>(value:U)->Maybe<U>{let result:Maybe<_>=wrap(value);result}
fn flag(value:Maybe<bool>)->bool{match value{Maybe::None=>false,Maybe::Some(x)=>x}}
fn read(value:Maybe<u64>)->u64{match value{Maybe::None=>0,Maybe::Some(x)=>x}}
pub fn answer()->u64{
    if flag(outer(true)) && !flag(empty()) && read(empty())==0{
        read(outer(0xfedcba9876543210))
    }else{0}
}

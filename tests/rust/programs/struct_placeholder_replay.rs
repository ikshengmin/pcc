struct Boxed<T>{value:T}
fn wrap<U>(value:U)->Boxed<U>{Boxed::<_>{value:value}}
fn outer<U>(value:U)->Boxed<U>{let boxed:Boxed<_>=wrap(value);boxed}
pub fn answer()->u64{
    let flag=outer(true);
    let wide=outer(0xfedcba9876543210);
    let small=outer(9u32);
    if flag.value && small.value==9u32{wide.value}else{0}
}

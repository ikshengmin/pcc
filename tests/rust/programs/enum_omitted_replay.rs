enum Value<T>{None,Some(T)}
fn wrap<U>(x:U)->Value<U>{Value::Some(x)}
fn empty<U>()->Value<U>{Value::None}
fn unwrap<U>(x:Value<U>,fallback:U)->U{
    match x{Value::Some(value)=>value,Value::None=>fallback}
}
pub fn answer()->u64{
    let a=unwrap(wrap(true),false);
    let b=unwrap(wrap(17u32),0u32);
    let c=unwrap(empty(),25u64);
    if a && b==17u32 && c==25u64{unwrap(wrap(0xfedcba9876543210),0)}else{0}
}

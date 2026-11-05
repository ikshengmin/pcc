struct R<T>{value:T}
fn wrap<U>(value:U)->R<U>{R{value:value}}
fn identity<U>(value:U)->U{wrap(value).value}
pub fn answer()->u64{
    if identity(true) && identity(9u32)==9u32{
        identity(0xfedcba9876543210)
    }else{0}
}

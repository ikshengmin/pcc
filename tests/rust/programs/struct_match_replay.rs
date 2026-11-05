struct R<T>{value:T}
fn identity<U>(value:U)->U{
    match (R::<_>{value:value}){r=>r.value}
}
pub fn answer()->u64{
    let result=match (R::<_>{value:identity(0xfedcba9876543210)}){r=>r.value};
    if identity(true) && identity(9u32)==9u32{result}else{0}
}

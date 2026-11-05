struct R<T>{value:T}
pub fn answer()->u64{
    let mut r=R::<_>{value:R::<_>{value:1}};
    match r{_=>()}
    r=R::<_>{value:R::<_>{value:0xfedcba9876543210u64}};
    match r{_=>()}
    r.value.value
}

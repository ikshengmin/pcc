fn id<T>(x:T)->T{x}fn outer<U>(x:U)->U{id::<_>(x)}
pub fn answer()->u64{
    let explicit=outer::<i32>(42);
    let inferred=outer(0xfedcba9876543210u64);
    if explicit==42{inferred}else{0}
}

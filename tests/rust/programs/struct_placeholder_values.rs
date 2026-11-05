struct Boxed<T>{value:T}
fn id<T>(value:T)->T{value}
fn take(value:Boxed<u64>)->u64{value.value}
pub fn answer()->u64{
    let wide=id(Boxed::<_>{value:0xfedcba9876543210});
    let flag=Boxed::<_>{value:true};
    let small=Boxed::<_>{value:3u32};
    if flag.value && small.value==3u32{take(wide)}else{0}
}

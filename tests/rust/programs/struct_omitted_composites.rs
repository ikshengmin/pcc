struct R<T>{value:T}
fn empty()->R<[u64;0]>{R{value:[]}}
pub fn answer()->u64{
    let nested=R{value:R{value:([0xfedcba9876543210,2],true)}};
    if empty().value.len()==0 && nested.value.value.1{
        nested.value.value.0[0]
    }else{0}
}

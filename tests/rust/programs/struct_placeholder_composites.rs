struct Boxed<T>{value:T}
struct Pair<T,U>{first:T,second:U}
fn empty<T>()->[T;0]{[]}
fn count(value:[bool;0])->usize{value.len()}
pub fn answer()->u64{
    let pair=Pair::<_,bool>{second:true,first:7u64};
    let array=Boxed::<[_;2]>{value:[1u64,2]};
    let nested=Boxed::<Boxed<(_,_)>>{value:Boxed::<_>{value:(0xfedcba9876543210,true)}};
    let zero=Boxed::<[_;0]>{value:empty()};
    if pair.first==7u64 && pair.second && array.value[1]==2u64 && count(zero.value)==0 && nested.value.value.1{
        nested.value.value.0
    }else{0}
}

struct Boxed<T>{value:T}
fn id<T>(value:T)->T{value}
fn empty<T>()->[T;0]{[]}
pub fn answer()->u64{
    let pair=id::<(_,_)>((5u64,true));
    let array=id::<[_;2]>([1u64,2]);
    let boxed=id::<Boxed<(_,_)>>(Boxed::<(u64,bool)>{value:(0xfedcba9876543210,true)});
    let zero:[bool;0]=empty::<_>();
    if pair.0==5u64 && pair.1 && array[0]==1u64 && array[1]==2u64 && zero.len()==0 && boxed.value.1{
        boxed.value.0
    }else{0}
}

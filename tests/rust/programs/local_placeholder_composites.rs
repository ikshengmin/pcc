struct Boxed<T>{value:T}
enum Maybe<T>{None,Some(T)}
fn make<T>(value:T)->Boxed<T>{Boxed::<T>{value:value}}
fn empty<T>()->[T;0]{[]}
fn none<T>()->Maybe<T>{Maybe::<T>::None}
fn take_empty(value:[bool;0])->usize{value.len()}
pub fn answer()->u64{
    let pair:(_,bool)=(5u64,true);
    let array:[_;2]=[1u64,2];
    let boxed:Boxed<(_,_)>=make((0xfedcba9876543210,true));
    let zero:[_;0]=empty();
    let mut maybe:Maybe<_>=none();
    maybe=Maybe::<u64>::Some(42);
    let found=match maybe{Maybe::<u64>::None=>0,Maybe::<u64>::Some(n)=>n};
    if pair.0==5u64 && pair.1 && array[1]==2u64 && take_empty(zero)==0 && found==42u64 && boxed.value.1{
        boxed.value.0
    }else{0}
}

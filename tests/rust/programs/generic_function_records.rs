#![no_std]
struct Wrap<T>{value:T}
struct Pair{first:u64,second:u64}
fn wrap<T>(value:T)->Wrap<T> { Wrap::<T>{value:value} }
fn extract<T>(value:Wrap<T>)->T { value.value }
fn identity<T>(value:T)->T { value }
fn answer()->u64 {
    let pair=Pair{first:0xfedcba9876543210u64,second:7};
    let wrapped=wrap::<Pair>(pair);
    let moved=identity::<Wrap<Pair>>(wrapped);
    extract::<Pair>(moved).first
}

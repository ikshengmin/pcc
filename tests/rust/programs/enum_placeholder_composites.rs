struct Wrapped<T>{value:T}
enum Either<T,U>{First(T),Second(U)}
fn read(value:Either<Wrapped<(u64,bool)>,(u64,u64)>)->u64{
    match value{
        Either::First(x)=>if x.value.1{x.value.0}else{0},
        Either::Second(x)=>x.0+x.1,
    }
}
pub fn answer()->u64{
    let a=Either::<Wrapped<(_,_)>,(_,_)>::First(Wrapped::<_>{value:(0xfedcba9876543210,true)});
    let b=Either::<Wrapped<(_,_)>,(_,_)>::Second((1,2));
    if read(b)==3{read(a)}else{0}
}

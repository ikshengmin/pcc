fn id<T>(x:T)->T{x}
fn pair<A,B>(a:A,b:B)->(A,B){(a,b)}
fn empty<T>()->[T;0]{[]}
fn wrap<U>(x:U)->U{id::<>(id::<>(x))}
fn plain(x:u64)->u64{x}
pub fn answer()->u64{
    let value=wrap::<>(plain::<>(0xfedcba9876543210));
    let data=pair::<>(value,true);
    let zero:[u64;0]=empty::<>();
    let array=id::<>([value,7u64]);
    if data.1 && zero.len::<>()==0 && array.len::<>()==2 && array[1]==7 {
        data.0
    }else{0}
}

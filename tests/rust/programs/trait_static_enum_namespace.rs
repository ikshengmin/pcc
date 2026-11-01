enum E{V(u64),Other(u64)}
trait T{fn V(x:u64)->Self;}
impl T for E{fn V(x:u64)->Self{E::Other(x+1)}}
fn f()->u64{match E::V(41){E::V(x)=>x,E::Other(x)=>x}}
enum G<X>{V(X),Other(X)}
impl T for G<u64>{fn V(x:u64)->Self{Self::Other(x+1)}}
pub fn answer()->u64 {
    let plain=f();
    let explicit=match <E as T>::V(41){E::V(x)=>x,E::Other(x)=>x};
    let generic=match G::<u64>::V(41){G::V(x)=>x,G::Other(x)=>x};
    let generic_explicit=match <G<u64> as T>::V(41){G::V(x)=>x,G::Other(x)=>x};
    if plain==41 && explicit==42 && generic==41 && generic_explicit==42 {
        0xfedcba9876543210u64
    } else {0}
}

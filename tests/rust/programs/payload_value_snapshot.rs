#![no_std]
enum E { Empty, Value(u64) }
fn choose(x:bool)->E {if x{E::Value(0xfedcba9876543210)}else{E::Empty}}
fn identity(x:E)->E{x}
pub extern "C" fn answer()->u64 {
    let mut e=choose(true);
    let result=match identity(e) {E::Empty=>0,E::Value(x)=>x};
    e=E::Empty;
    match e{E::Empty=>result,E::Value(_)=>0}
}

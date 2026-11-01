#![no_std]
fn forever<T>()->T{forever()}
fn head<T>(value:(T,))->T{value.0}
fn id<T>(value:T)->T{value}
fn direct(flag:bool)->u64{
    if flag{head(forever())}else{head(id((0xfedcba9876543210,)))}
}
fn abstract_case<U>(value:(U,),flag:bool)->U{
    if flag{head(forever())}else{head(id(value))}
}
pub extern "C" fn answer()->u64{
    let first=direct(false);
    let second=abstract_case((0xfedcba9876543210u64,),false);
    if first==second{first}else{0}
}

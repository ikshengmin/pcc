#![no_std]
fn combine<T,U>(first:T,second:U)->(T,U) {(first,second)}
fn answer()->u64 {
    let value=combine::<u64,u64>({return 0xfedcba9876543210u64;},
                                {while true {} 7});
    value.0
}

#![no_std]
fn identity<T>(value:T)->T {value}
fn combine<T,U>(first:T,second:U)->(T,U) {(first,second)}
fn answer()->u64 {
    let mut count=0;
    let pair=combine::<u64,u64>({count+=1;identity::<u64>(0xfedcba9876543210u64)},
                              {count+=2;identity::<u64>(7)});
    if count==3 && pair.1==7 {pair.0} else {0}
}

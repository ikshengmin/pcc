fn source(calls:&mut u64)->[u64;3] {
    *calls+=1;
    [1,2,3]
}
pub fn answer()->u64 {
    let mut calls=0u64;
    let mut total=0u64;
    for value in source(&mut calls) { total=total*10+value; }
    if calls!=1 || total!=123 { return 0; }
    0xfedcba9876543210u64
}

pub fn answer()->u64 {
    let mut total=0u64;
    for value in [1u64,2,3,4,5] {
        if value==2 { continue; }
        if value==4 { break; }
        total=total*10+value;
    }
    if total!=13 { return 0; }
    0xfedcba9876543210u64
}

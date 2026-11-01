pub fn answer()->u64 {
    let mut total=0u64;
    for mut value in [1u64,2,3] {
        value+=1;
        total=total*10+value;
    }
    if total!=234 { return 0; }
    0xfedcba9876543210u64
}

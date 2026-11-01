pub fn answer()->u64 {
    let mut values=[1u64,2,3];
    let mut total=0u64;
    for value in values {
        values=[9,9,9];
        total=total*10+value;
    }
    if total!=123 || values[1]!=9 { return 0; }
    0xfedcba9876543210u64
}

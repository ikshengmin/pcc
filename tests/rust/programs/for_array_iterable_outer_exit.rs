pub fn answer()->u64 {
    let mut total=0u64;
    for outer in [1u64,2,3,4] {
        for inner in {
            if outer==2 { continue; }
            if outer==3 { break; }
            [4u64,5]
        } {
            total=total*10+inner;
        }
        total=total*10+outer;
    }
    if total!=451 { return 0; }
    0xfedcba9876543210u64
}

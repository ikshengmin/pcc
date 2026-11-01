pub fn answer()->u64 {
    let mut total=0u64;
    for outer in [1u64,2,3] {
        let mut index=0u64;
        while index<3 {
            index+=1;
            if index==1 { continue; }
            if index==2 { break; }
        }
        total=total*10+outer;
        for inner in [4u64,5] {
            if inner==4 { continue; }
            total+=inner;
            break;
        }
    }
    if total!=678 { return 0; }
    0xfedcba9876543210u64
}

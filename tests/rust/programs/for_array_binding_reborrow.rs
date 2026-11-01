fn increase(value:&mut u64) { *value+=10; }
pub fn answer()->u64 {
    let mut total=0u64;
    for mut value in [1u64,2,3] {
        increase(&mut value);
        total=total*100+value;
    }
    if total!=111213 { return 0; }
    0xfedcba9876543210u64
}

struct Item { value:u64 }
fn count<T>(value:T)->u64 {
    let mut total=0u64;
    for element in [1u64,2,3] { total=total*10+element; }
    total
}
pub fn answer()->u64 {
    let record=count(Item{value:9});
    let scalar=count(7u64);
    if record!=123 || scalar!=123 { return 0; }
    0xfedcba9876543210u64
}

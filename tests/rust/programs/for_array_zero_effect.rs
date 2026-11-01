struct Item { value:u64 }
fn item(calls:&mut u64)->Item {
    *calls+=1;
    Item{value:9}
}
pub fn answer()->u64 {
    let mut calls=0u64;
    for _ in [item(&mut calls);0] { calls+=100; }
    if calls!=1 { return 0; }
    0xfedcba9876543210u64
}

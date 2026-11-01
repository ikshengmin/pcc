struct Item { value:u64 }
fn finish(calls:&mut u64,value:u64)->u64 { *calls+=1; value }
fn search(calls:&mut u64)->u64 {
    for outer in [Item{value:1},Item{value:2},Item{value:3}] {
        for inner in [10u64,20] {
            if outer.value==2 { return finish(calls,outer.value+inner); }
        }
    }
    0
}
pub fn answer()->u64 {
    let mut calls=0u64;
    let result=search(&mut calls);
    if calls!=1 || result!=12 { return 0; }
    0xfedcba9876543210u64
}

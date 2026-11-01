struct Item { value:u64 }
impl Item { fn take(self)->u64 { self.value } }
pub fn answer()->u64 {
    let mut values=[Item{value:1},Item{value:2},Item{value:3}];
    let mut total=0u64;
    for value in values { total=total*10+value.take(); }
    values=[Item{value:9},Item{value:8},Item{value:7}];
    if total!=123 || values[1].value!=8 { return 0; }
    0xfedcba9876543210u64
}

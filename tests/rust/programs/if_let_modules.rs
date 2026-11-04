mod left {
    pub enum Item { Present(u64), Missing }
    pub fn read(value: Item) -> u64 {
        if let Item::Present(item) = value { item } else { 1 }
    }
    pub fn value() -> u64 { read(Item::Present(40)) }
}
mod right {
    pub enum Item { Present(u64), Missing }
    pub fn read(value: Item) -> u64 {
        if let Item::Present(item) = value { item } else { 2 }
    }
    pub fn value() -> u64 { read(Item::Missing) }
}
pub fn answer() -> u64 {
    if left::value() + right::value() == 42 { 0xfedcba9876543210u64 } else { 0 }
}

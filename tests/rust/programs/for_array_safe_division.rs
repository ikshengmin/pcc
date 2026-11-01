fn quotient(value:i64)->i64 { value/3 }
pub fn answer()->u64 {
    let mut total=0i64;
    for value in [-12i64,15,21] { total+=quotient(value)+value%3; }
    if total!=8 { return 0; }
    0xfedcba9876543210u64
}

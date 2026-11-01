struct Pair<T> { value:T }
enum Choice { Value(u64), Empty }
pub fn answer()->u64 {
    let value=[(1u64,2u64),(3,4)];
    let mut total=0u64;
    for value in value { total=total*100+value.0*10+value.1; }
    let mut units=0u64;
    for _ in [();3] { units+=1; }
    let mut nested=0u64;
    for row in [[1u64,2],[3,4]] { for element in row { nested=nested*10+element; } }
    let mut nominal=0u64;
    for item in [Pair::<u64>{value:5},Pair::<u64>{value:6}] { nominal=nominal*10+item.value; }
    for item in [Choice::Value(7),Choice::Empty] { match item { Choice::Value(n)=>{nominal+=n;} Choice::Empty=>{} } }
    if total!=1234 || value[0].0!=1 || units!=3 || nested!=1234 || nominal!=63 { return 0; }
    0xfedcba9876543210u64
}

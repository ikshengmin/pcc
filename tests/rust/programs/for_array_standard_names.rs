struct IntoIterator { value:u64 }
struct Iterator { value:u64 }
enum Option { Some, None }
fn into_iter()->u64 { 1000 }
fn next()->u64 { 2000 }
pub fn answer()->u64 {
    let IntoIterator=[1u64,2,3];
    let Iterator=7u64;
    let Option=9u64;
    let mut total=0u64;
    for Option in IntoIterator { total=total*10+Option; }
    if total!=123 || Iterator!=7 || Option!=9 { return 0; }
    0xfedcba9876543210u64
}

mod values{
    pub fn id<T>(x:T)->T{x}
    pub fn wide()->u64{0xfedcba9876543210}
}
use values::id as pick;
fn wrap<U>(x:U)->U{values::id::<>(pick::<>(x))}
pub fn answer()->u64{wrap::<>(values::wide::<>())}

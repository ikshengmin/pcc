mod m{pub enum Maybe<T>{None,Some(T)}}
use m::Maybe as Value;
fn read(value:Value<(u64,bool)>)->u64{
    match value{Value::None=>0,Value::Some((x,true))=>x,Value::Some((_,false))=>0}
}
fn array(value:Value<[u64;2]>)->Value<[u64;2]>{value}
pub fn answer()->u64{
    let empty=Value::None;
    let full=Value::Some((0xfedcba9876543210,true));
    let payload=array(Value::Some([1,2]));
    if read(empty)==0{read(full)}else{0}
}

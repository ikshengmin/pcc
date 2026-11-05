enum Maybe<T>{None,Some(T)}
trait Build{fn Some(value:u64)->Self;}
impl Build for Maybe<u64>{fn Some(value:u64)->Self{Maybe::<u64>::Some(value+1)}}
fn read(value:Maybe<u64>)->u64{match value{Maybe::None=>0,Maybe::Some(x)=>x}}
fn none()->Maybe<u64>{Maybe::<_>::None}
pub fn answer()->u64{
    let x=Maybe::<_>::Some(0xfedcba9876543210);
    let y=Maybe::<_>::None;
    if read(y)==0 && read(none())==0 && read(<Maybe<u64> as Build>::Some(40))==41{read(x)}else{0}
}

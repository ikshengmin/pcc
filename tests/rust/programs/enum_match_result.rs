enum Maybe<T>{None,Some(T)}
fn none()->u64{match Maybe::<_>::None{Maybe::None=>0,Maybe::Some(x)=>x}}
pub fn answer()->u64{
    let mut count=0;
    let value=match {count+=1;Maybe::<_>::Some(0xfedcba9876543210)}{
        Maybe::None=>0,
        Maybe::Some(x)=>x,
    };
    if count==1 && none()==0{value}else{0}
}

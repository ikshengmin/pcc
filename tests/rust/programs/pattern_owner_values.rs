enum Choice<T,U>{First(T),Second(U),Empty}
fn read(value:Choice<u64,bool>)->u64{
    match value{
        Choice::<_,bool>::First(x)=>x,
        Choice::<u64,_>::Second(false)=>2,
        Choice::<_,_>::Second(true)=>3,
        Choice::<_,_>::Empty=>4,
    }
}
pub fn answer()->u64{
    if read(Choice::<_,_>::Second(false))==2 && read(Choice::<_,_>::Second(true))==3 && read(Choice::<_,_>::Empty)==4{
        read(Choice::<_,_>::First(0xfedcba9876543210))
    }else{0}
}

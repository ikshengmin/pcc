enum Choice<T,U>{First(T),Second(U),Empty}
pub fn answer()->u64{
    let first=match Choice::<_,_>::First(0xfedcba9876543210){
        Choice::First(x)=>x,
        Choice::Second(false)=>0,
        Choice::Second(true)=>1,
        Choice::Empty=>2,
    };
    let empty=match Choice::<_,_>::Empty{
        Choice::First(())=>0,
        Choice::Second(false)=>1,
        Choice::Second(true)=>2,
        Choice::Empty=>3,
    };
    if empty==3{first}else{0}
}

fn same<T>(value:T)->T{let _=value;value}
struct Wrap<T>{value:T}
pub fn answer()->u64{
    let _:Wrap<[u64;0]>=Wrap{value:[]};
    let _:(u64,bool)=(4294967296,true);
    if same(true) && same(17u32)==17u32{same(Wrap{value:0xfedcba9876543210}).value}else{0}
}

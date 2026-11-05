struct Record<T,U>{value:T,flag:U}
fn get()->u64{
    match (Record::<_,_>{value:0xfedcba9876543210,flag:true}){
        r=>if r.flag{r.value}else{0}
    }
}
fn same()->Record<u64,bool>{
    match (Record::<_,_>{value:0xfedcba9876543210,flag:true}){r=>r}
}
pub fn answer()->u64{
    let r=same();
    if r.flag && r.value==get(){get()}else{0}
}

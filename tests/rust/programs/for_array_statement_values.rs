pub fn answer()->u64 {
    let mut total=0u64;
    for flag in [true,false] {
        let value=if flag{[1u64,2]}else if true{[3,4]}else{[5,6]}[0];
        let length=if flag{[1u64,2]}else if false{[3,4]}else{[5,6]}.len();
        total=total*10+value+length as u64;
    }
    if total!=35 { return 0; }
    0xfedcba9876543210u64
}

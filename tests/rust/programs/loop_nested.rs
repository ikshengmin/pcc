pub fn answer() -> u64 {
    let mut outer = 0u64;
    let mut sum = 0u64;
    loop {
        outer += 1;
        let mut inner = 0u64;
        loop {
            inner += 1;
            if inner == 1 { continue; }
            sum += inner;
            break;
        }
        while true {
            sum += 12;
            break;
        }
        if outer == 3 { break; }
    }
    if sum == 42 && outer == 3 { 0xfedcba9876543210u64 } else { 0 }
}

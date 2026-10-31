#![no_std]

#[repr(C)]
struct AbiRecord {
    tag: i32,
    wide: u64,
    signed_value: i64,
}

#[no_mangle]
pub extern "C" fn rust_read_record(record: &AbiRecord) -> u64 {
    record.wide
        + (record.signed_value as u64) * 3u64
        + ((record.tag as i64) as u64) * 5u64
}

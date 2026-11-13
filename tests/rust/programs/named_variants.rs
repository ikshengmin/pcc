// Original PCC specification fixture: struct-like enum variants (Rust
// Reference items/enumerations.html), as uv-lock declares
// `UnsupportedVersion { supported: u32, version: u32 }`: construction with
// shorthand and source-order field evaluation, named patterns with `..`,
// renaming and literals, `Self::` paths, and derived equality.
#[derive(Clone, Copy, PartialEq)]
enum LockError { UnsupportedVersion { supported: u32, version: u32 }, Missing, Parse(u32) }
impl LockError {
    fn unsupported(version: u32) -> Self { Self::UnsupportedVersion { version, supported: 1 } }
    fn code(self) -> u32 {
        match self {
            Self::UnsupportedVersion { supported, version: v } => supported * 100 + v,
            Self::Missing => 7,
            Self::Parse(n) => n,
        }
    }
}
pub fn answer() -> &'static str {
    let mut order = 0u32;
    let ordered = LockError::UnsupportedVersion {
        version: { order = order * 10 + 1; order },
        supported: { order = order * 10 + 2; order },
    };
    let ok = LockError::unsupported(5).code() == 105 && LockError::Missing.code() == 7
        && LockError::Parse(3).code() == 3
        && ordered == LockError::UnsupportedVersion { supported: 12, version: 1 }
        && matches!(ordered, LockError::UnsupportedVersion { version: 1, .. })
        && !matches!(LockError::Missing, LockError::UnsupportedVersion { .. });
    if ok { "named variants agree" } else { "named variants differ" }
}

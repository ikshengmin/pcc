mod provider {
    pub struct Row { pub value:u64 }
    pub fn make()->Row { Row { value:0xfedcba9876543210u64 } }
}
use crate::provider::Row as Value;
use crate::provider::make as build;
pub fn answer()->u64 { let item:Value=build(); item.value }

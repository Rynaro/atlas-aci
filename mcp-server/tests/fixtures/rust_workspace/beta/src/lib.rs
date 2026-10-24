pub fn free() {}
pub struct Other;
impl Other { pub fn execute(&self) { free(); } }

pub mod inline { pub fn inline_fn() {} }
mod external;
pub struct Alpha;
#[derive(Clone)] pub struct Beta;
pub struct Wrapper<T>(T);
pub enum Choice { A, B }
pub trait Act {
    fn execute(&self);
    fn defaulted(&self) { free(); }
}
impl Alpha {
    fn execute(&self) { fn nested() {} nested(); }
    fn associated() {}
}
impl Beta { fn execute(&self) {} }
impl Act for Alpha { fn execute(&self) { self.execute(); } }
impl<T> Wrapper<T> where T: Clone { fn execute(&self) {} }
// Repeated same-header blocks deliberately test syntax identity, not compilation.
impl Alpha { fn repeat(&self) {} }
impl Alpha { fn repeat(&self) {} }
pub fn free() {}
fn generic<T>() {}
fn factory() -> fn() { free }
pub fn exercise<T: Act>(obj: T) {
    free(); Alpha::associated(); obj.execute(); generic::<u8>(); (factory())();
}
#[cfg(feature = "optional")] pub fn cfg_observed() {}
#[some_proc_macro] pub fn decorated() {}
macro_rules! generated { () => { fn generated_only() {} } }
generated!();
include!("not_materialized.rs");

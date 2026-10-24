struct Thing; enum Choice { A, B }
trait Action { fn run(&self); fn defaulted(&self) {} }
impl Thing { fn new() -> Self { Thing } fn run(&self) { fn nested() {} nested(); } }
impl Action for Thing { fn run(&self) { self.run(); Thing::new(); generic::<u8>(); } }
fn generic<T>() {} fn free() { generic::<u8>(); }
macro_rules! generated { () => { fn hidden() {} } }
generated!();

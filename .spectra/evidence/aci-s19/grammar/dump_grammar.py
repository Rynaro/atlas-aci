"""ACI-S19 Day-1 grammar qualification: dump locked-grammar trees for each map row.

Run: python dump_grammar.py > dump.txt   (inside the dev image, same tree-sitter pins)
"""

from __future__ import annotations

import importlib.metadata as md

from tree_sitter_language_pack import get_parser

SAMPLES: dict[str, dict[str, str]] = {
    "python": {
        "function": "async def area(w: int, h: int = 2, *a, **k) -> int:\n    # c\n    return w * h  # t\n",
        "decorated": "@dec(1)\n# comment between\n@other\ndef f(a):\n    pass\n\nclass K:\n    @staticmethod\n    def m(): ...\n",
        "class": "@dc\nclass K[T](Base, metaclass=M):\n    x = 1\n    def m(self):\n        return 1\n",
        "empty": "def f(): pass\ndef g(): ...\ndef h():\n    '''doc'''\n",
        "dedent_a": "def f(x):\n    if x:\n        a()\n        b()\n",
        "dedent_b": "def f(x):\n    if x:\n        a()\n    b()\n",
        "strings": 'def f():\n    return "a b"\ndef g():\n    return f"x {y}  z"\ndef h():\n    return """\n  multi  \n"""\nx = 1 \\\n  + 2\n',
        "comments": "def f(a, # c1\n      b):  # c2\n    # c3\n    return a\n    # c4\n",
        "error": "def f(:\n    return 1\n",
    },
    "javascript": {
        "function": "async function* gen(a, b = 1, ...r) {\n  // c\n  return a; /* t */\n}\n",
        "export": "export function f(a) { return a }\nexport default function g() {}\nexport default class {}\nexport class C { static async m(a) { return 1 } get x() { return 1 } set x(v) {} *g() {} #p() {} }\n",
        "class": "class A extends B { constructor(a) { super(a) } m() {} }\n",
        "decorators": "@dec class D { @m @n x() {} }\n@dec export class E {}\nexport @dec class F {}\n",
        "strings": "function f() { return 'a b' }\nfunction g() { return `a ${x}  b` }\nfunction h() { return /a  b/g }\n",
        "arrow": "const f = (a) => a;\nconst g = a => a;\n",
        "jsx": "function C() { return <div> hello  world </div>; }\n",
        "empty": "function f() {}\nclass E {}\nclass G { m() {} }\n",
    },
    "typescript": {
        "function": "export async function f<T>(a: T, b?: number): Promise<T> {\n  // c\n  return a;\n}\n",
        "overload": "function o(a: string): string;\nfunction o(a: number): number;\nfunction o(a: any): any { return a }\ndeclare function dd(a: number): void;\n",
        "class": "export default abstract class C<T> extends B<T> implements I, J {\n  private static readonly x: number = 1;\n  public async m<U>(a: U): Promise<void> { }\n  protected abstract n(): void;\n  get v(): number { return 1 }\n  set v(x: number) {}\n  constructor(private a: string) { super() }\n  override o(): void {}\n  #p() {}\n}\n",
        "decorators": "@Component({a: 1})\nexport class D {\n  @Input() x: string;\n  @HostListener('a')\n  m(@Inject(T) a: T) {}\n}\n@dec export class E {}\nexport @dec class F {}\n",
        "method_sig": "interface I { m(a: number): void; }\nabstract class A { abstract m(a: number): void; }\nclass B { m(a: number): void; m(a: any) {} }\n",
        "strings": "function f() { return 'a b' }\nfunction g() { return `a ${x}  b` }\nfunction h(): string { return \"x  y\" }\n",
        "empty": "function f() {}\nclass E {}\nclass G { m() {} }\n",
    },
    "tsx": {
        "arrow": "export const A = (a: number): number => a * 2;\nconst B = async <T,>(a: T) => { return a };\nlet C = function (x) { return x };\nconst D = x => x;\nexport default function E() { return <div> a  b </div>; }\nexport class F { m() { return <b/> } }\n",
        "multi_decl": "const a = () => 1, b = () => 2;\n",
        "typed": "const f: Fn = (a) => a;\n",
        "comments": "// lead\nexport const A = (/* p */ a) => /* b */ a; // t\n",
    },
    "ruby": {
        "method": "def area(w, h = 1, *a, k: 1, &b)\n  # c\n  w * h # t\nend\n",
        "endless": "def sq(x) = x * x\ndef nothing; end\n",
        "singleton": "def self.build(a)\n  new(a)\nend\ndef obj.m; 1; end\n",
        "visibility": "class K\n  private def a; end\n  public def b; 1; end\n  protected def c() = 2\n  module_function def d; end\n  private_class_method def self.e; end\n  private\n  def f; end\nend\n",
        "class": "class A < B\n  def m; 1; end\nend\nclass A::B < C::D; end\nmodule M\n  def x; end\nend\nmodule N::O; end\n",
        "setter_op": "class K\n  def x=(v); @x = v; end\n  def +(o); 1; end\n  def [](i) = i\nend\n",
        "strings": "def f\n  'a b'\nend\ndef g\n  \"a #{x}  b\"\nend\ndef h\n  <<~EOS\n    x  y\n  EOS\nend\ndef i\n  %w[a  b]\nend\n",
        "rescue": "def f\n  1\nrescue => e\n  2\nensure\n  3\nend\n",
    },
    "rust": {
        "function": "#[inline]\n/// doc\npub(crate) async fn f<T: Clone>(a: T, b: &str) -> Option<T> where T: Send {\n    // c\n    Some(a)\n}\n",
        "impl": "impl<T> Foo<T> {\n    #[must_use]\n    pub fn m(&self) -> i32 { 1 }\n    fn n() {}\n}\ntrait Tr: Sized {\n    fn s(&self);\n    fn d(&self) { }\n}\n",
        "struct_enum": "#[derive(Debug)]\npub struct S { pub a: i32, b: String }\nstruct U;\nstruct T(i32, i32);\n#[derive(Clone)]\nenum E { A, B(i32), C { x: i32 } }\n",
        "mod": "mod x;\n#[cfg(test)]\nmod tests { fn t() {} }\npub mod y {}\n",
        "trait": "pub trait T<A>: Sized where A: Copy { type X; const C: i32; fn f(&self); }\n",
        "strings": "fn f() { let a = \"a  b\"; let b = r#\"x  y\"#; let c = 'c'; let d = b\"q\"; }\n",
        "comments": "// c0\nfn f(/* p */ a: i32) /* r */ -> i32 { /* b */ a } // t\n/// doc\nfn g() {}\n",
        "macro": "fn f() { println!(\"a  b {}\", 1); }\nmacro_rules! m { () => {} }\n",
    },
    "bash": {
        "function": "f() {\n  # c\n  echo a  b\n}\nfunction g { echo 1; }\nfunction h() { echo 2; }\n",
        "strings": "f() { echo \"a  b\" 'c  d' $(x  y); cat <<EOF\n  h  i\nEOF\n}\n",
        "newlines": "f() {\n  a\n  b\n}\ng() {\n  a; b\n}\n",
        "empty": "f() { :; }\ng() { true; }\n",
    },
    "scss": {
        "mixin": "@mixin m($a, $b: 1) {\n  // c\n  color: $a;\n}\n@function f($x) { @return $x * 2; }\n@mixin n { a: b }\n",
        "others": "%ph { color: red; }\n$var: 1px;\n.cls { a: b }\n#idn { a: b }\n",
        "comments": "/* c */\n@mixin m($a /* p */) { x: y /* q */; }\n",
    },
    "html": {"id": '<div id="a" class="b">x</div>\n'},
    "yaml": {"key": "a:\n  b: 1\nc: 2\n"},
    "markdown": {"heading": "# Title\n\ntext\n\nSub\n---\n"},
}


def dump(node, depth: int = 0, field: str | None = None, source: bytes = b"") -> list[str]:
    flags = []
    if node.is_missing:
        flags.append("MISSING")
    if node.has_error and not node.is_named:
        flags.append("HAS_ERROR")
    text = ""
    if node.child_count == 0 or node.type in {"string", "string_content", "comment"}:
        text = " " + repr(source[node.start_byte : node.end_byte].decode("utf-8", "replace"))
    prefix = f"{field}: " if field else ""
    named = "" if node.is_named else "~"
    out = [
        f"{'  ' * depth}{prefix}{named}{node.type} "
        f"[{node.start_byte}:{node.end_byte}]{(' ' + ','.join(flags)) if flags else ''}{text}"
    ]
    for index, child in enumerate(node.children):
        out.extend(dump(child, depth + 1, node.field_name_for_child(index), source))
    return out


def main() -> None:
    print(
        "tree-sitter",
        md.version("tree-sitter"),
        "tree-sitter-language-pack",
        md.version("tree-sitter-language-pack"),
    )
    for lang, groups in SAMPLES.items():
        parser = get_parser(lang)  # type: ignore[arg-type]
        for name, text in groups.items():
            source = text.encode("utf-8")
            tree = parser.parse(source)
            print(f"\n===== {lang} / {name} (root has_error={tree.root_node.has_error}) =====")
            print(text.rstrip("\n"))
            print("-----")
            print("\n".join(dump(tree.root_node, 0, None, source)))


if __name__ == "__main__":
    main()

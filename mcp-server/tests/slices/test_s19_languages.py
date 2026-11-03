"""ACI-S19 per-row qualification: every `parsed` map row, plus the raw_text kinds.

Each case indexes a real before/after pair and asserts the category of the named
entities. `unchanged` means no record at all (counted only).
"""

from __future__ import annotations

import pytest
from test_s19_support import diff, records

PY = "a.py"
JS = "a.js"
TS = "a.ts"
TSX = "a.tsx"
RB = "a.rb"
RS = "a.rs"
SH = "a.sh"
SCSS = "a.scss"


def case(identifier, path, first, second, expect, basis="parsed"):
    return pytest.param(path, first, second, expect, basis, id=identifier)


CASES = [
    # ---- python function / class -------------------------------------------------
    case(
        "py-sig",
        PY,
        "def f(a):\n    return a\n",
        "def f(a, b=1):\n    return a\n",
        {"f": "signature"},
    ),
    case(
        "py-body", PY, "def f(a):\n    return a\n", "def f(a):\n    return a + 1\n", {"f": "body"}
    ),
    case(
        "py-format",
        PY,
        "def f(a):\n    # c\n    return a\n",
        "def f(a):\n    # d\n    return  a\n",
        {"f": "format"},
    ),
    case(
        "py-shift",
        PY,
        "def f(a):\n    return a\n",
        "\n\n# note\ndef f(a):\n    return a\n",
        {"f": "location"},
    ),
    case(
        "py-string-space",
        PY,
        'def f():\n    return "a b"\n',
        'def f():\n    return "a  b"\n',
        {"f": "body"},
    ),
    case(
        "py-decorator",
        PY,
        "@dec\ndef f(a):\n    return a\n",
        "@other\ndef f(a):\n    return a\n",
        {"f": "signature"},
    ),
    case(
        "py-class-member-edit",
        PY,
        "class K:\n    def m(self):\n        return 1\n",
        "class K:\n    def m(self):\n        return 2\n",
        {"K": "unchanged", "m": "body"},
    ),
    case(
        "py-class-member-added",
        PY,
        "class K:\n    def m(self):\n        return 1\n",
        "class K:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 2\n",
        {"K": "body", "n": "added", "m": "unchanged"},
    ),
    case(
        "py-class-signature",
        PY,
        "class K(A):\n    x = 1\n",
        "class K(B):\n    x = 1\n",
        {"K": "signature"},
    ),
    case(
        "py-decorated-method-in-container",
        PY,
        "class K:\n    @dec\n    def m(self):\n        return 1\n",
        "class K:\n    @dec2\n    def m(self):\n        return 1\n",
        {"K": "unchanged", "m": "signature"},
    ),
    # ---- javascript function / method / class -------------------------------------
    case(
        "js-fn-sig",
        JS,
        "function f(a) {\n  return a;\n}\n",
        "function f(a, b) {\n  return a;\n}\n",
        {"f": "signature"},
    ),
    case(
        "js-fn-body",
        JS,
        "function f(a) {\n  return a;\n}\n",
        "function f(a) {\n  return a + 1;\n}\n",
        {"f": "body"},
    ),
    case(
        "js-fn-format",
        JS,
        "function f(a) {\n  // c\n  return a;\n}\n",
        "function f(a) {\n  // d\n  return  a;\n}\n",
        {"f": "format"},
    ),
    case(
        "js-fn-shift",
        JS,
        "function f(a) {\n  return a;\n}\n",
        "\n// note\n\nfunction f(a) {\n  return a;\n}\n",
        {"f": "location"},
    ),
    case(
        "js-fn-string-space",
        JS,
        'function f() {\n  return "a b";\n}\n',
        'function f() {\n  return "a  b";\n}\n',
        {"f": "body"},
    ),
    case(
        "js-fn-export",
        JS,
        "export function f(a) {\n  return a;\n}\n",
        "function f(a) {\n  return a;\n}\n",
        {"f": "signature"},
    ),
    case(
        "js-method-sig",
        JS,
        "class A {\n  m(a) {\n    return a;\n  }\n}\n",
        "class A {\n  m(a, b) {\n    return a;\n  }\n}\n",
        {"m": "signature", "A": "unchanged"},
    ),
    case(
        "js-method-body",
        JS,
        "class A {\n  m(a) {\n    return a;\n  }\n}\n",
        "class A {\n  m(a) {\n    return a + 1;\n  }\n}\n",
        {"m": "body", "A": "unchanged"},
    ),
    case(
        "js-method-static",
        JS,
        "class A {\n  m(a) {\n    return a;\n  }\n}\n",
        "class A {\n  static m(a) {\n    return a;\n  }\n}\n",
        {"m": "signature"},
    ),
    case(
        "js-method-format",
        JS,
        "class A {\n  m(a) {\n    // c\n    return a;\n  }\n}\n",
        "class A {\n  m(a) {\n    // d\n    return a;\n  }\n}\n",
        {"m": "format", "A": "unchanged"},
    ),
    case(
        "js-class-member-added",
        JS,
        "class A {\n  m(a) {\n    return a;\n  }\n}\n",
        "class A {\n  m(a) {\n    return a;\n  }\n  n(b) {\n    return b;\n  }\n}\n",
        {"A": "body", "n": "added", "m": "unchanged"},
    ),
    case(
        "js-class-heritage",
        JS,
        "class A extends B {\n  x() {}\n}\n",
        "class A extends C {\n  x() {}\n}\n",
        {"A": "signature"},
    ),
    case(
        "js-class-decorator",
        JS,
        "@dec class A {\n  x() {}\n}\n",
        "@dec2 class A {\n  x() {}\n}\n",
        {"A": "signature"},
    ),
    case(
        "js-class-shift",
        JS,
        "class A {\n  m() {}\n}\n",
        "\n\n// note\nclass A {\n  m() {}\n}\n",
        {"A": "location", "m": "location"},
    ),
    # ---- typescript -----------------------------------------------------------------
    case(
        "ts-fn-sig",
        TS,
        "function f(a: number): number {\n  return a;\n}\n",
        "function f(a: string): number {\n  return a;\n}\n",
        {"f": "signature"},
    ),
    case(
        "ts-fn-return-type",
        TS,
        "function f(a: number): number {\n  return a;\n}\n",
        "function f(a: number): string {\n  return a;\n}\n",
        {"f": "signature"},
    ),
    case(
        "ts-fn-body",
        TS,
        "function f(a: number): number {\n  return a;\n}\n",
        "function f(a: number): number {\n  return a * 2;\n}\n",
        {"f": "body"},
    ),
    case(
        "ts-fn-format",
        TS,
        "function f(a: number): number {\n  // c\n  return a;\n}\n",
        "function f(a: number): number {\n  /* d */\n  return a;\n}\n",
        {"f": "format"},
    ),
    case(
        "ts-fn-export",
        TS,
        "export function f(a: number) {\n  return a;\n}\n",
        "export default function f(a: number) {\n  return a;\n}\n",
        {"f": "signature"},
    ),
    case(
        "ts-fn-shift",
        TS,
        "function f(a: number) {\n  return a;\n}\n",
        "\n\n// note\nfunction f(a: number) {\n  return a;\n}\n",
        {"f": "location"},
    ),
    case(
        "ts-fn-template-space",
        TS,
        "function f(a: number) {\n  return `a ${a} b`;\n}\n",
        "function f(a: number) {\n  return `a ${a}  b`;\n}\n",
        {"f": "body"},
    ),
    case(
        "ts-method-sig",
        TS,
        "class A {\n  m(a: number): number {\n    return a;\n  }\n}\n",
        "class A {\n  m(a: string): number {\n    return a;\n  }\n}\n",
        {"m": "signature", "A": "unchanged"},
    ),
    case(
        "ts-method-modifier",
        TS,
        "class A {\n  m(a: number) {\n    return a;\n  }\n}\n",
        "class A {\n  private m(a: number) {\n    return a;\n  }\n}\n",
        {"m": "signature"},
    ),
    case(
        "ts-method-body",
        TS,
        "class A {\n  m(a: number) {\n    return a;\n  }\n}\n",
        "class A {\n  m(a: number) {\n    return a * 2;\n  }\n}\n",
        {"m": "body", "A": "unchanged"},
    ),
    case(
        "ts-method-decorator",
        TS,
        "class A {\n  @Dec()\n  m(a: number) {}\n}\n",
        "class A {\n  @Dec2()\n  m(a: number) {}\n}\n",
        {"m": "signature", "A": "unchanged"},
    ),
    case(
        "ts-class-implements",
        TS,
        "class A extends B {\n  x() {}\n}\n",
        "class A extends B implements I {\n  x() {}\n}\n",
        {"A": "signature"},
    ),
    case(
        "ts-class-export-decorator",
        TS,
        "@Component({a: 1})\nexport class D {\n  x() {}\n}\n",
        "@Component({a: 2})\nexport class D {\n  x() {}\n}\n",
        {"D": "signature"},
    ),
    case(
        "ts-class-member-added",
        TS,
        "class A {\n  m(a: number) {\n    return a;\n  }\n}\n",
        "class A {\n  m(a: number) {\n    return a;\n  }\n  n() {}\n}\n",
        {"A": "body", "n": "added", "m": "unchanged"},
    ),
    # ---- tsx declarators and components -----------------------------------------------
    case(
        "tsx-arrow-sig",
        TSX,
        "export const A = (a: number): number => a * 2;\n",
        "export const A = (a: string): number => a * 2;\n",
        {"A": "signature"},
    ),
    case(
        "tsx-arrow-body",
        TSX,
        "export const A = (a: number): number => a * 2;\n",
        "export const A = (a: number): number => a * 3;\n",
        {"A": "body"},
    ),
    case(
        "tsx-arrow-format",
        TSX,
        "export const A = (a: number): number => /* c */ a * 2;\n",
        "export const A = (a: number): number => /* d */ a  *  2;\n",
        {"A": "format"},
    ),
    case(
        "tsx-arrow-keyword",
        TSX,
        "export const A = (a: number): number => a * 2;\n",
        "export let A = (a: number): number => a * 2;\n",
        {"A": "signature"},
    ),
    case(
        "tsx-arrow-export",
        TSX,
        "export const A = (a: number): number => a * 2;\n",
        "const A = (a: number): number => a * 2;\n",
        {"A": "signature"},
    ),
    case(
        "tsx-arrow-shift",
        TSX,
        "const A = (a: number) => a * 2;\n",
        "\n\n// note\nconst A = (a: number) => a * 2;\n",
        {"A": "location"},
    ),
    case(
        "tsx-arrow-string-space",
        TSX,
        'const A = (a: number) => "a b";\n',
        'const A = (a: number) => "a  b";\n',
        {"A": "body"},
    ),
    case(
        "tsx-single-param-arrow",
        TSX,
        "const A = x => x * 2;\n",
        "const A = (x) => x * 2;\n",
        {"A": "signature"},
    ),
    case(
        "tsx-jsx-text-space",
        TSX,
        "export default function E() {\n  return <div> a b </div>;\n}\n",
        "export default function E() {\n  return <div> a  b </div>;\n}\n",
        {"E": "body"},
    ),
    case(
        "tsx-multi-declarator",
        TSX,
        "const a = () => 1, b = () => 2;\n",
        "const a = () => 1, b = () => 3;\n",
        {"a": "unchanged", "b": "body"},
    ),
    # ---- ruby ------------------------------------------------------------------------------
    case(
        "rb-sig",
        RB,
        "def area(w, h)\n  w * h\nend\n",
        "def area(w, h, s)\n  w * h\nend\n",
        {"area": "signature"},
    ),
    case(
        "rb-body",
        RB,
        "def area(w, h)\n  w * h\nend\n",
        "def area(w, h)\n  w * h * 2\nend\n",
        {"area": "body"},
    ),
    case(
        "rb-format",
        RB,
        "def area(w, h)\n  # c\n  w * h\nend\n",
        "def area(w, h)\n  # d\n  w  *  h\nend\n",
        {"area": "format"},
    ),
    case(
        "rb-shift",
        RB,
        "def area(w, h)\n  w * h\nend\n",
        "\n\n# note\ndef area(w, h)\n  w * h\nend\n",
        {"area": "location"},
    ),
    case(
        "rb-string-space",
        RB,
        'def f\n  "a b"\nend\n',
        'def f\n  "a  b"\nend\n',
        {"f": "body"},
    ),
    case(
        "rb-heredoc-space",
        RB,
        "def f\n  <<~EOS\n    x y\n  EOS\nend\n",
        "def f\n  <<~EOS\n    x  y\n  EOS\nend\n",
        {"f": "body"},
    ),
    case(
        "rb-endless",
        RB,
        "def sq(x) = x * x\n",
        "def sq(x) = x * x * x\n",
        {"sq": "body"},
    ),
    case(
        "rb-singleton",
        RB,
        "def self.build(a)\n  new(a)\nend\n",
        "def self.build(a, b)\n  new(a)\nend\n",
        {"build": "signature"},
    ),
    case(
        "rb-visibility-changed",
        RB,
        "class K\n  private def a\n    1\n  end\nend\n",
        "class K\n  public def a\n    1\n  end\nend\n",
        {"a": "signature", "K": "unchanged"},
    ),
    case(
        "rb-visibility-added",
        RB,
        "class K\n  def a\n    1\n  end\nend\n",
        "class K\n  private def a\n    1\n  end\nend\n",
        {"a": "signature", "K": "unchanged"},
    ),
    case(
        "rb-class-member-edit",
        RB,
        "class A < B\n  def m\n    1\n  end\nend\n",
        "class A < B\n  def m\n    2\n  end\nend\n",
        {"A": "unchanged", "m": "body"},
    ),
    case(
        "rb-class-superclass",
        RB,
        "class A < B\n  def m\n    1\n  end\nend\n",
        "class A < C\n  def m\n    1\n  end\nend\n",
        {"A": "signature"},
    ),
    case(
        "rb-class-member-added",
        RB,
        "class A\n  def m\n    1\n  end\nend\n",
        "class A\n  def m\n    1\n  end\n\n  def n\n    2\n  end\nend\n",
        {"A": "body", "n": "added", "m": "unchanged"},
    ),
    case(
        "rb-module-member-added",
        RB,
        "module M\n  def x\n    1\n  end\nend\n",
        "module M\n  def x\n    1\n  end\n\n  def y\n    2\n  end\nend\n",
        {"M": "body", "y": "added", "x": "unchanged"},
    ),
    # ---- rust -------------------------------------------------------------------------------
    case(
        "rs-fn-sig",
        RS,
        "fn f(a: i32) -> i32 {\n    a\n}\n",
        "fn f(a: i64) -> i32 {\n    a\n}\n",
        {"f": "signature"},
    ),
    case(
        "rs-fn-body",
        RS,
        "fn f(a: i32) -> i32 {\n    a\n}\n",
        "fn f(a: i32) -> i32 {\n    a + 1\n}\n",
        {"f": "body"},
    ),
    case(
        "rs-fn-format",
        RS,
        "fn f(a: i32) -> i32 {\n    // c\n    a\n}\n",
        "fn f(a: i32) -> i32 {\n    /* d */\n    a\n}\n",
        {"f": "format"},
    ),
    case(
        "rs-fn-shift",
        RS,
        "fn f(a: i32) -> i32 {\n    a\n}\n",
        "\n// note\n\nfn f(a: i32) -> i32 {\n    a\n}\n",
        {"f": "location"},
    ),
    case(
        "rs-fn-string-space",
        RS,
        'fn f() {\n    let s = "a b";\n}\n',
        'fn f() {\n    let s = "a  b";\n}\n',
        {"f": "body"},
    ),
    case(
        "rs-fn-attribute-changed",
        RS,
        "#[inline]\nfn f() {}\n",
        "#[inline(always)]\nfn f() {}\n",
        {"f": "signature"},
    ),
    case(
        "rs-fn-attribute-added",
        RS,
        "fn f() {}\n",
        "#[inline]\nfn f() {}\n",
        {"f": "signature"},
    ),
    case(
        "rs-fn-where-clause",
        RS,
        "fn f<T>(a: T) -> T\nwhere\n    T: Clone,\n{\n    a\n}\n",
        "fn f<T>(a: T) -> T\nwhere\n    T: Copy,\n{\n    a\n}\n",
        {"f": "signature"},
    ),
    case(
        "rs-method-body",
        RS,
        "struct S;\nimpl S {\n    fn m(&self) -> i32 {\n        1\n    }\n}\n",
        "struct S;\nimpl S {\n    fn m(&self) -> i32 {\n        2\n    }\n}\n",
        {"m": "body", "S": "unchanged"},
    ),
    case(
        "rs-method-attribute",
        RS,
        "struct S;\nimpl S {\n    #[must_use]\n    fn m(&self) -> i32 {\n        1\n    }\n}\n",
        "struct S;\nimpl S {\n    #[inline]\n    fn m(&self) -> i32 {\n        1\n    }\n}\n",
        {"m": "signature"},
    ),
    case(
        "rs-struct-field",
        RS,
        "pub struct S {\n    pub a: i32,\n}\n",
        "pub struct S {\n    pub a: i64,\n}\n",
        {"S": "signature"},
    ),
    case(
        "rs-struct-format",
        RS,
        "pub struct S {\n    // c\n    pub a: i32,\n}\n",
        "pub struct S {\n    // d\n    pub a: i32,\n}\n",
        {"S": "format"},
    ),
    case(
        "rs-enum-variant",
        RS,
        "enum E {\n    A,\n}\n",
        "enum E {\n    A,\n    B,\n}\n",
        {"E": "signature"},
    ),
    case(
        "rs-trait-member-added",
        RS,
        "trait T {\n    fn a(&self);\n}\n",
        "trait T {\n    fn a(&self);\n    fn b(&self);\n}\n",
        {"T": "body", "b": "added", "a": "unchanged"},
    ),
    case(
        "rs-trait-method-signature",
        RS,
        "trait T {\n    fn a(&self);\n}\n",
        "trait T {\n    fn a(&self, x: i32);\n}\n",
        {"a": "signature", "T": "unchanged"},
    ),
    case(
        "rs-mod-member-edit",
        RS,
        "mod m {\n    fn t() {\n        1;\n    }\n}\n",
        "mod m {\n    fn t() {\n        2;\n    }\n}\n",
        {"m": "unchanged", "t": "body"},
    ),
    case(
        "rs-mod-declaration",
        RS,
        "mod x;\n",
        "pub mod x;\n",
        {"x": "signature"},
    ),
    case(
        "rs-mod-member-added",
        RS,
        "mod m {\n    fn t() {}\n}\n",
        "mod m {\n    fn t() {}\n    fn u() {}\n}\n",
        {"m": "body", "u": "added", "t": "unchanged"},
    ),
    # ---- bash ---------------------------------------------------------------------------------
    case("sh-sig", SH, "f() {\n  echo a\n}\n", "function f {\n  echo a\n}\n", {"f": "signature"}),
    case("sh-body", SH, "f() {\n  echo a\n}\n", "f() {\n  echo b\n}\n", {"f": "body"}),
    case(
        "sh-format",
        SH,
        "f() {\n  # c\n  echo a\n}\n",
        "f() {\n  # d\n  echo   a\n}\n",
        {"f": "format"},
    ),
    case(
        "sh-shift",
        SH,
        "f() {\n  echo a\n}\n",
        "\n# note\n\nf() {\n  echo a\n}\n",
        {"f": "location"},
    ),
    case(
        "sh-string-space",
        SH,
        'f() {\n  echo "a b"\n}\n',
        'f() {\n  echo "a  b"\n}\n',
        {"f": "body"},
    ),
    case(
        "sh-heredoc-indent",
        SH,
        "f() {\n  cat <<EOF\n  x y\nEOF\n}\n",
        "f() {\n  cat <<EOF\nx y\nEOF\n}\n",
        {"f": "body"},
    ),
    # ---- scss ---------------------------------------------------------------------------------
    case(
        "scss-mixin-sig",
        SCSS,
        "@mixin m($a) {\n  color: $a;\n}\n",
        "@mixin m($a, $b) {\n  color: $a;\n}\n",
        {"m": "signature"},
    ),
    case(
        "scss-mixin-body",
        SCSS,
        "@mixin m($a) {\n  color: $a;\n}\n",
        "@mixin m($a) {\n  color: red;\n}\n",
        {"m": "body"},
    ),
    case(
        "scss-mixin-format",
        SCSS,
        "@mixin m($a) {\n  // c\n  color: $a;\n}\n",
        "@mixin m($a) {\n  // d\n  color:   $a;\n}\n",
        {"m": "format"},
    ),
    case(
        "scss-mixin-shift",
        SCSS,
        "@mixin m($a) {\n  color: $a;\n}\n",
        "\n// note\n\n@mixin m($a) {\n  color: $a;\n}\n",
        {"m": "location"},
    ),
    case(
        "scss-string-space",
        SCSS,
        '@mixin m {\n  content: "a b";\n}\n',
        '@mixin m {\n  content: "a  b";\n}\n',
        {"m": "body"},
    ),
    case(
        "scss-function",
        SCSS,
        "@function f($x) {\n  @return $x;\n}\n",
        "@function f($x, $y) {\n  @return $x;\n}\n",
        {"f": "signature"},
    ),
    case(
        "scss-function-body",
        SCSS,
        "@function f($x) {\n  @return $x;\n}\n",
        "@function f($x) {\n  @return $x * 2;\n}\n",
        {"f": "body"},
    ),
    case(
        "scss-descendant-selector",
        SCSS,
        "@mixin m {\n  .a .b {\n    x: y;\n  }\n}\n",
        "@mixin m {\n  .a.b {\n    x: y;\n  }\n}\n",
        {"m": "body"},
    ),
    case(
        "rb-block-comment",
        RB,
        "def f\n=begin\nnote\n=end\n  1\nend\n",
        "def f\n=begin\nnote2\n=end\n  1\nend\n",
        {"f": "format"},
    ),
    case(
        "py-line-continuation",
        PY,
        "def f():\n    return 1 + \\\n        2\n",
        "def f():\n    return 1 + 2\n",
        {"f": "format"},
    ),
    case(
        "py-fstring-space",
        PY,
        'def f(y):\n    return f"x {y}  z"\n',
        'def f(y):\n    return f"x {y} z"\n',
        {"f": "body"},
    ),
    case(
        "js-regex-space",
        JS,
        "function f() {\n  return /a  b/g;\n}\n",
        "function f() {\n  return /a b/g;\n}\n",
        {"f": "body"},
    ),
    case(
        "rs-raw-string-space",
        RS,
        'fn f() {\n    let s = r#"x  y"#;\n}\n',
        'fn f() {\n    let s = r#"x y"#;\n}\n',
        {"f": "body"},
    ),
    case(
        "rs-macro-string-space",
        RS,
        'fn f() {\n    println!("a  b {}", 1);\n}\n',
        'fn f() {\n    println!("a b {}", 1);\n}\n',
        {"f": "body"},
    ),
    # ---- raw_text kinds --------------------------------------------------------------------------
    case(
        "raw-scss-variable-shift",
        SCSS,
        "$v: 1px;\n",
        "// note\n$v: 1px;\n",
        {"$v": "location"},
        "raw_text",
    ),
    case(
        "raw-scss-variable-text",
        SCSS,
        "$v: 1px;\n",
        "$v: 2px;\n",
        {"$v": "unknown:fingerprint_unsupported_kind"},
        "raw_text",
    ),
    case(
        "raw-yaml-shift",
        "a.yml",
        "a: 1\nb: 2\n",
        "# top\na: 1\nb: 2\n",
        {"a": "location", "b": "location"},
        "raw_text",
    ),
    case(
        "raw-yaml-text",
        "a.yml",
        "a: 1\nb: 2\n",
        "a: 9\nb: 2\n",
        {"a": "unknown:fingerprint_unsupported_kind", "b": "unchanged"},
        "raw_text",
    ),
    case(
        "raw-markdown-shift",
        "a.md",
        "# Title\n\ntext\n",
        "intro\n\n# Title\n\ntext\n",
        {"Title": "location"},
        "raw_text",
    ),
    case(
        "raw-markdown-text",
        "a.md",
        "# Title\n\ntext\n",
        "## Title\n\ntext\n",
        {"Title": "unknown:fingerprint_unsupported_kind"},
        "raw_text",
    ),
    case(
        "raw-html-shift",
        "a.html",
        '<div id="a">x</div>\n',
        '<!-- note -->\n<div id="a">x</div>\n',
        {"a": "location"},
        "raw_text",
    ),
    case(
        "raw-html-text",
        "a.html",
        '<div id="a">x</div>\n',
        "<div id='a'>x</div>\n",
        {"a": "unknown:fingerprint_unsupported_kind"},
        "raw_text",
    ),
]

FLAGS = {
    "signature": {"signature_changed": True},
    "body": {"signature_changed": False, "body_changed": True},
    "format": {"signature_changed": False, "body_changed": False, "text_changed": True},
    "location": {"text_changed": False, "position_changed": True},
}


@pytest.mark.parametrize(("path", "first", "second", "expect", "basis"), CASES)
def test_language_row(tmp_path, path, first, second, expect, basis):
    change_set = diff(tmp_path, {path: first}, {path: second})
    assert not change_set["partial_paths"]
    for name, want in expect.items():
        found = records(change_set, name=name, path=path)
        if want == "unchanged":
            assert not found, (name, found)
            continue
        assert len(found) == 1, (name, found, change_set["changes"])
        record = found[0]
        if want.startswith("unknown:"):
            assert record["category"] == "unknown" and record["reason"] == want[8:]
            assert record["before"]["fingerprint_basis"] == "raw_text"
            continue
        assert record["category"] == want, (name, record["category"])
        for flag, value in FLAGS.get(want, {}).items():
            assert record[flag] is value, (name, flag)
        if record["before"] and record["after"]:
            assert record["before"]["fingerprint_basis"] == basis
            assert record["after"]["fingerprint_basis"] == basis

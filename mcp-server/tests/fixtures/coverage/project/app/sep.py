# ruff: noqa: RUF001
SEPARATOR = "a b"


def parts():
    return SEPARATOR.split("\u2028")


def marker():
    return "x y"  # end

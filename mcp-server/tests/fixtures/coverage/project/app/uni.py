"""Non-ASCII content: ünïcödé, 日本語 and an emoji 🎉."""

GREETING = "héllo wörld — 日本語 🎉"


def shout(text):
    return text.upper() + "!"  # naïve


def width():
    return len("日本語")

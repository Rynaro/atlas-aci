"""Arithmetic helpers used by the fixture tests."""

import functools


def traced(func):
    """Decorator that counts every call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        wrapper.calls += 1
        return func(*args, **kwargs)

    wrapper.calls = 0
    return wrapper


@traced
def add(a, b):
    return a + b


def total(values):
    result = 0
    for value in values:
        result = add(
            result,
            value,
        )
    return result


def table(n):
    return {
        "double": [x * 2 for x in range(n)],
        "square": {x: x * x for x in range(n)},
        "label": str(
            n,
        ),
    }


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def never_called():
    return "unreached"

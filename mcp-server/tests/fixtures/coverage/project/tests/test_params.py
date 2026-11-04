import pytest
from app import calc, uni


@pytest.mark.parametrize("value", [0, 5])
def test_clamp_param(value):
    assert calc.clamp(value, 1, 4) in {1, 4}


@pytest.mark.parametrize("raw", ["a|b", "plain"])
def test_pipe(raw):
    assert uni.shout(raw).endswith("!")


@pytest.mark.parametrize("text", ["héllo", "日本"])
def test_unicode_param(text):
    assert uni.shout(text)

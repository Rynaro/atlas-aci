import asyncio

from app import aio, crlf_mod, ff_mod, sep, uni


def test_crlf():
    assert crlf_mod.double(2) == 4
    assert crlf_mod.pair(1, 2) == (1, 2)


def test_form_feed():
    assert ff_mod.second() == 2


def test_separator():
    assert sep.parts() == ["a", "b"]
    assert uni.width() == 3


def test_async():
    assert asyncio.run(aio.gather_all([1, 2])) == [2, 4]

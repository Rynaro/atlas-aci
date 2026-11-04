"""Async helpers."""

import asyncio


async def fetch(n):
    await asyncio.sleep(0)
    return n * 2


async def gather_all(items):
    return await asyncio.gather(
        *[fetch(i) for i in items],
    )

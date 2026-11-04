import pytest
from app import calc


@pytest.fixture
def numbers():
    warm = calc.add(0, 0)
    data = [1, 2, 3, warm]
    yield data
    data.clear()

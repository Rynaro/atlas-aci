from app import calc


def test_add(numbers):
    assert calc.add(1, 2) == 3


class TestTotals:
    def test_total(self, numbers):
        assert calc.total(numbers) == 6

    def test_table(self):
        assert calc.table(3)["square"][2] == 4


def test_clamp():
    assert calc.clamp(5, 0, 3) == 3
    assert calc.clamp(-1, 0, 3) == 0

from app.research.cost_stress import break_even


def test_break_even_interpolates_where_returns_cross_zero():
    assert break_even([1, 2, 3], [20.0, 10.0, -10.0]) == 2.5


def test_break_even_edges():
    assert break_even([1, 2], [-5.0, -10.0]) == 0.0      # loses even at modeled costs
    assert break_even([1, 2], [30.0, 5.0]) is None       # still profitable at the highest multiple

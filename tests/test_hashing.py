"""Canonical result-set hash (ADR-011)."""

from datetime import date
from decimal import Decimal

from evals.hashing import has_top_level_order, result_hash


def h(rows, ordered=False):
    return result_hash(rows, ordered=ordered)


def test_char_padding_is_not_a_difference():
    assert h([("0000000042 ", "JAL")]) == h([("0000000042", "JAL")])


def test_numbers_hash_by_value_not_by_type_or_scale():
    assert h([(3,)]) == h([(Decimal("3.0000"),)]) == h([(3.0,)]) == h([("3",)])
    assert h([(Decimal("0.00"),)]) == h([(0,)]) == h([(Decimal("-0.0000"),)])
    assert h([(Decimal("100"),)]) == h([(100,)])


def test_different_values_hash_differently():
    assert h([(Decimal("10.5"),)]) != h([(Decimal("10.51"),)])
    assert h([("JAL",)]) != h([("NLE",)])
    assert h([(None,)]) != h([("",)])


def test_rows_are_sorted_unless_the_query_is_ordered():
    a, b = ("A", 1), ("B", 2)
    assert h([a, b]) == h([b, a])
    assert h([a, b], ordered=True) != h([b, a], ordered=True)


def test_dates_and_columns_by_position():
    assert h([(date(2003, 5, 1),)]) == h([("2003-05-01",)])
    assert h([("a", "b")]) != h([("b", "a")])


def test_top_level_order_detection():
    assert has_top_level_order("SELECT a FROM t ORDER BY a")
    assert has_top_level_order("WITH x AS (SELECT 1 a) SELECT a FROM x ORDER BY a DESC")
    assert has_top_level_order("SELECT a FROM t UNION ALL SELECT a FROM u ORDER BY 1")
    assert not has_top_level_order("SELECT a FROM t")
    assert not has_top_level_order("SELECT a FROM (SELECT a FROM t ORDER BY a) s")
    assert not has_top_level_order("WITH x AS (SELECT a FROM t ORDER BY a) SELECT a FROM x")

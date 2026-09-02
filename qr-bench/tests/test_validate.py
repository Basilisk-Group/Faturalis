"""Tests for the low-level primitives in qr_bench.validate.

Flag *decisions* (which codes fire, with what message) are tested in
tests/test_flags.py against qr_bench.flags.evaluate_flags - this file only
covers the reusable numeric/date helpers validate.py still owns.
"""

from qr_bench.validate import amounts_equal, rates_for_region, validate_date


def test_amounts_equal_tolerates_formatting_differences():
    assert amounts_equal("10.5", "10.50")
    assert amounts_equal("3.25", "3.25")


def test_amounts_equal_rejects_real_differences():
    assert not amounts_equal("10.50", "15.00")


def test_amounts_equal_within_rounding_tolerance():
    assert amounts_equal("6.90", "6.91")


def test_amounts_equal_both_missing_counts_as_equal():
    assert amounts_equal(None, None)
    assert amounts_equal("", "")


def test_amounts_equal_one_missing_is_not_equal():
    assert not amounts_equal("10.00", None)
    assert not amounts_equal(None, "10.00")


def test_validate_date_accepts_yyyymmdd():
    assert validate_date("20240115")


def test_validate_date_rejects_garbage():
    assert not validate_date("20241332")
    assert not validate_date("not-a-date")
    assert not validate_date(None)
    assert not validate_date("")


def test_rates_for_region_defaults_to_pt():
    assert rates_for_region(None) == rates_for_region("PT")
    assert rates_for_region("XX") == rates_for_region("PT")


def test_rates_for_region_pt_ma_differs_from_mainland():
    assert rates_for_region("PT-MA") != rates_for_region("PT")
    assert rates_for_region("PT-MA")["normal"] == 0.22


def test_rates_for_region_pt_ac_differs_from_mainland():
    assert rates_for_region("PT-AC")["normal"] == 0.16

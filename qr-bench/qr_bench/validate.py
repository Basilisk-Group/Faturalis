"""Low-level validation primitives shared by qr_bench.flags and the sample
generator. Flag *decisions* (which codes fire, with what message) live in
qr_bench/flags.py - this module only holds reusable numeric/date checks.
"""

from datetime import datetime

TOLERANCE_EUR = 0.02

BASE_KEYS = ("I2", "I3", "I5", "I7")
VAT_KEYS = ("I4", "I6", "I8")

# Field key pair -> tax bracket name, per rate bracket. Shared with the
# sample generator (scripts/generate_samples.py) so both sides agree on
# which I-fields carry which rate - avoids the two drifting apart the way
# the NIF checksum logic did before it was made a single source of truth.
RATE_BRACKETS: list[tuple[str, str, str]] = [
    ("I3", "I4", "reduced"),
    ("I5", "I6", "intermediate"),
    ("I7", "I8", "normal"),
]

# VAT rate table selected by I1 (tax region). Madeira and the Azores have
# their own reduced regional rates; anything else, or a missing I1, falls
# back to mainland PT rates.
RATE_TABLES: dict[str, dict[str, float]] = {
    "PT": {"reduced": 0.06, "intermediate": 0.13, "normal": 0.23},
    "PT-MA": {"reduced": 0.05, "intermediate": 0.12, "normal": 0.22},
    "PT-AC": {"reduced": 0.04, "intermediate": 0.09, "normal": 0.16},
}
DEFAULT_TAX_REGION = "PT"


def rates_for_region(region: str | None) -> dict[str, float]:
    return RATE_TABLES.get(region or DEFAULT_TAX_REGION, RATE_TABLES[DEFAULT_TAX_REGION])


def nif_checksum(nif: str) -> bool:
    """Portuguese NIF check-digit validation (mod-11).

    9 digits; weighted sum of the first 8 digits using weights 9 down to 2;
    remainder = sum % 11; expected check digit is 0 if remainder < 2,
    otherwise 11 - remainder; must match the 9th digit.
    """
    if not nif or not nif.isdigit() or len(nif) != 9:
        return False
    digits = [int(c) for c in nif]
    weighted_sum = sum(d * w for d, w in zip(digits[:8], range(9, 1, -1)))
    remainder = weighted_sum % 11
    expected_check_digit = 0 if remainder < 2 else 11 - remainder
    return digits[8] == expected_check_digit


def parse_amount(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def amounts_equal(a: str | None, b: str | None, tolerance: float = TOLERANCE_EUR) -> bool:
    """Compares two amount strings for equality, tolerant of formatting
    differences (e.g. '10.5' vs '10.50') but not of an actual different value.
    """
    pa, pb = parse_amount(a), parse_amount(b)
    if pa is None or pb is None:
        return pa == pb
    return abs(pa - pb) <= tolerance


def validate_date(date_str: str | None) -> bool:
    if not date_str:
        return False
    try:
        datetime.strptime(date_str, "%Y%m%d")
        return True
    except ValueError:
        return False

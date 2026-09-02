from qr_bench.validate import nif_checksum


def _build_valid_nif(first8: list[int]) -> str:
    weighted_sum = sum(d * w for d, w in zip(first8, range(9, 1, -1)))
    remainder = weighted_sum % 11
    check = 0 if remainder < 2 else 11 - remainder
    return "".join(map(str, first8 + [check]))


def test_valid_nifs_pass_checksum():
    for first8 in ([5, 0, 0, 0, 0, 0, 0, 1], [1, 2, 3, 4, 5, 6, 7, 8], [9, 9, 9, 9, 9, 9, 9, 9], [2, 0, 0, 0, 0, 0, 0, 0]):
        nif = _build_valid_nif(first8)
        assert nif_checksum(nif), f"{nif} should be valid"


def test_wrong_check_digit_fails():
    nif = _build_valid_nif([1, 2, 3, 4, 5, 6, 7, 8])
    bad_digit = str((int(nif[-1]) + 1) % 10)
    bad_nif = nif[:-1] + bad_digit
    assert not nif_checksum(bad_nif)


def test_wrong_length_fails():
    assert not nif_checksum("12345")
    assert not nif_checksum("1234567890")


def test_non_digit_characters_fail():
    assert not nif_checksum("12345678A")


def test_empty_or_none_fails():
    assert not nif_checksum("")
    assert not nif_checksum(None)


def test_remainder_less_than_two_gives_zero_check_digit():
    # weighted sum chosen so that sum % 11 is 0 or 1, exercising the
    # "check digit 0 if result >= 10" branch explicitly.
    first8 = [1, 0, 0, 0, 0, 0, 0, 0]  # weighted_sum = 9, remainder = 9 -> not this branch
    # Search for a first8 combo landing on remainder 0 or 1 to hit the branch directly.
    for a in range(10):
        candidate = [a, 0, 0, 0, 0, 0, 0, 0]
        weighted_sum = sum(d * w for d, w in zip(candidate, range(9, 1, -1)))
        if weighted_sum % 11 in (0, 1):
            first8 = candidate
            break
    nif = _build_valid_nif(first8)
    assert nif[-1] == "0"
    assert nif_checksum(nif)

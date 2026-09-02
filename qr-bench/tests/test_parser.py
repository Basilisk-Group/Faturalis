from qr_bench.parser import parse_qr_string


def test_parses_full_field_set():
    raw = (
        "A:123456789*B:987654321*C:PT*D:FT*E:N*F:20240115*G:FT A/123*"
        "H:12345678-1*I1:PT*I2:5.00*I3:10.00*I4:0.60*I5:20.00*I6:2.60*"
        "I7:30.00*I8:6.90*N:10.10*O:65.10*Q:ABCD*R:1234"
    )
    fields = parse_qr_string(raw)
    assert fields["A"] == "123456789"
    assert fields["I8"] == "6.90"
    assert fields["Q"] == "ABCD"
    assert len(fields) == 20


def test_missing_i_fields_are_absent_not_positional():
    raw = "A:123456789*C:PT*D:FT*E:N*F:20240115*G:1*H:1-1*I1:PT*I7:30.00*I8:6.90*N:6.90*O:36.90*Q:ABCD*R:1"
    fields = parse_qr_string(raw)
    assert "I2" not in fields
    assert "I3" not in fields
    assert "I4" not in fields
    assert "B" not in fields
    assert fields.get("I2") is None
    assert fields["I7"] == "30.00"
    assert fields["I8"] == "6.90"


def test_split_on_first_colon_only_preserves_rest():
    raw = "H:12345678-1:extra*A:123456789"
    fields = parse_qr_string(raw)
    assert fields["H"] == "12345678-1:extra"
    assert fields["A"] == "123456789"


def test_empty_string_returns_empty_dict():
    assert parse_qr_string("") == {}


def test_ignores_parts_without_colon():
    raw = "A:123456789*garbage*C:PT"
    fields = parse_qr_string(raw)
    assert "garbage" not in fields
    assert fields["A"] == "123456789"
    assert fields["C"] == "PT"


def test_field_p_withholding_tax_is_captured_when_present():
    raw = "A:123456789*F:20240115*O:100.00*P:5.00*Q:ABCD"
    fields = parse_qr_string(raw)
    assert fields["P"] == "5.00"


def test_field_p_is_absent_when_not_present():
    raw = "A:123456789*F:20240115*O:100.00*Q:ABCD"
    fields = parse_qr_string(raw)
    assert "P" not in fields


def test_never_assumes_field_order():
    raw_forward = "A:1*B:2*C:3"
    raw_reversed = "C:3*B:2*A:1"
    assert parse_qr_string(raw_forward) == parse_qr_string(raw_reversed)

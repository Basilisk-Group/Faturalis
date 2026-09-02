"""Parser for the Portuguese fiscal QR code payload.

Format: key:value pairs separated by "*", e.g. "A:123456789*B:...*F:20240115*...".
Fields I2..I8 are optional and only present when the corresponding VAT rate is
used on the document, so callers must never assume a fixed field order or
position - always look fields up by key.
"""

KNOWN_FIELDS = {
    "A": "emitter_nif",
    "B": "acquirer_nif",
    "C": "country",
    "D": "doc_type",
    "E": "status",
    "F": "doc_date",
    "G": "doc_number",
    "H": "atcud",
    "I1": "tax_region",
    "I2": "exempt_base",
    "I3": "reduced_base",
    "I4": "reduced_vat",
    "I5": "intermediate_base",
    "I6": "intermediate_vat",
    "I7": "normal_base",
    "I8": "normal_vat",
    "N": "total_tax",
    "O": "doc_total",
    "P": "withholding_tax",
    "Q": "hash",
    "R": "cert_number",
}


def parse_qr_string(raw: str) -> dict[str, str]:
    """Split a raw QR payload into a key -> value dict.

    Splits on "*" to get each field, then splits each field on the FIRST
    ":" only, since values (dates, hashes, ATCUD) may themselves contain
    non-delimiter punctuation. Parts without a colon are ignored - they
    are not valid key:value fields.
    """
    fields: dict[str, str] = {}
    if not raw:
        return fields
    for part in raw.split("*"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        if not key:
            continue
        fields[key] = value.strip()
    return fields

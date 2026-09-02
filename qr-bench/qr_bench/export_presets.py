"""Column preset registry for exports.

Config, not code: adding an ERP-specific preset (Primavera, Sage, PHC,
TOConline, ...) later means adding a new PRESETS entry here, never
touching qr_bench/exporter.py. Each column:
  key: how exporter.resolve_raw_value looks up a value - "field_a".."field_r"
       map to the matching QR letter code (via effective, correction-aware
       fields), a few special keys (client_name, supplier_name, status,
       bases_total) are handled explicitly.
  header_pt: column header (exports are Portuguese-only, like flag text)
  type: "text" | "date" | "currency" - drives CSV/XLSX formatting
"""

PRESETS: dict[str, dict] = {
    "generico": {
        "label_pt": "Genérico",
        "columns": [
            {"key": "client_name", "header_pt": "Cliente", "type": "text"},
            {"key": "field_a", "header_pt": "NIF Emitente", "type": "text"},
            {"key": "supplier_name", "header_pt": "Fornecedor", "type": "text"},
            {"key": "field_f", "header_pt": "Data", "type": "date"},
            {"key": "field_d", "header_pt": "Tipo", "type": "text"},
            {"key": "field_g", "header_pt": "Número do Documento", "type": "text"},
            {"key": "field_h", "header_pt": "ATCUD", "type": "text"},
            {"key": "bases_total", "header_pt": "Base Tributável", "type": "currency"},
            {"key": "field_n", "header_pt": "IVA", "type": "currency"},
            {"key": "field_p", "header_pt": "Retenção", "type": "currency"},
            {"key": "field_o", "header_pt": "Total", "type": "currency"},
            {"key": "status", "header_pt": "Estado", "type": "text"},
        ],
    },
}

DEFAULT_PRESET = "generico"

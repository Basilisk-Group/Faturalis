"""Registry of every validation/observation flag qr-bench can raise on a
document, plus the pure logic that decides which ones apply.

FLAGS is the single source of truth for flag copy - GET /api/flags exposes
it verbatim so the frontend (and the /glossario page) never hardcode flag
text. Each document stores only `{"code": ..., "detail": {...}}` per flag
(see qr_bench/db.py); the human-readable message is rendered on read from
`explanation_pt` + `detail`, so the registry copy can be edited without a
data migration.

FORNECEDOR_DESCONHECIDO is deliberately NOT decided here: the VIES lookup
that determines it runs asynchronously, after the document row already
exists (see qr_bench/suppliers.py), so it can't be known at the moment
evaluate_flags() runs. It's computed separately, at read time, by
evaluate_supplier_flag() in qr_bench/db.py's join with the suppliers table.
"""

from datetime import date, datetime

from qr_bench.validate import (
    BASE_KEYS,
    RATE_BRACKETS,
    TOLERANCE_EUR,
    VAT_KEYS,
    amounts_equal,
    nif_checksum,
    parse_amount,
    rates_for_region,
)

SEVERITIES = ("erro", "aviso", "info")
_SEVERITY_RANK = {"erro": 3, "aviso": 2, "info": 1}

CONSUMIDOR_FINAL_NIF = "999999990"

REGION_NAMES = {
    "PT": "Continente",
    "PT-MA": "Madeira",
    "PT-AC": "Açores",
}

FLAGS: dict[str, dict[str, str]] = {
    "DOCUMENTO_ANULADO": {
        "code": "DOCUMENTO_ANULADO",
        "severity": "info",
        "label_pt": "Documento marcado como anulado",
        "explanation_pt": "O campo de estado do documento (E) indica que este documento foi anulado pelo emitente.",
        "cause_pt": "Isto acontece quando o emitente cancela ou substitui o documento após a sua emissão original.",
        "action_pt": "Confirme se existe um documento de substituição e não contabilize este valor duas vezes.",
    },
    "DUPLICADO_CONFLITUOSO": {
        "code": "DUPLICADO_CONFLITUOSO",
        "severity": "erro",
        "label_pt": "ATCUD duplicado com conflito",
        "explanation_pt": "Este ATCUD já tinha sido visto noutro documento, mas com um total diferente: {total_existente} no documento anterior e {total_atual} neste.",
        "cause_pt": "Normalmente ocorre quando um documento foi corrigido e reemitido sem alterar o ATCUD, ou quando há um erro de leitura num dos dois documentos.",
        "action_pt": "Compare os dois documentos manualmente antes de aceitar qualquer um dos valores.",
    },
    "QR_ILEGIVEL": {
        "code": "QR_ILEGIVEL",
        "severity": "erro",
        "label_pt": "Código QR ilegível",
        "explanation_pt": "Foi detetado um código QR na imagem, mas não foi possível ler o seu conteúdo mesmo após {tentativas} tentativas de descodificação.",
        "cause_pt": "Costuma dever-se a fotografias desfocadas, mal enquadradas, com pouca luz ou com reflexos sobre o código.",
        "action_pt": "Tente fotografar o documento novamente com melhor iluminação e o código QR bem enquadrado.",
    },
    "TOTAIS_INCONSISTENTES": {
        "code": "TOTAIS_INCONSISTENTES",
        "severity": "erro",
        "label_pt": "Totais do documento inconsistentes",
        "explanation_pt": "Bases ({bases}) + imposto ({imposto}) = {soma}, mas o documento indica {total}.",
        "cause_pt": "Pode dever-se a um erro de arredondamento do software de faturação ou a um erro de leitura de um dos campos.",
        "action_pt": "Verifique os valores no documento original antes de usar este total nas contas.",
    },
    "IVA_INCONSISTENTE": {
        "code": "IVA_INCONSISTENTE",
        "severity": "erro",
        "label_pt": "Valor de IVA inconsistente",
        "explanation_pt": "A soma do IVA por taxa ({soma_iva}) não corresponde ao total de imposto indicado no documento ({total_imposto}).",
        "cause_pt": "Costuma acontecer por erro de arredondamento ou porque um dos campos de IVA foi lido incorretamente.",
        "action_pt": "Confirme o valor de IVA no documento original antes de o lançar.",
    },
    "TAXA_IVA_ATIPICA": {
        "code": "TAXA_IVA_ATIPICA",
        "severity": "aviso",
        "label_pt": "Taxa de IVA atípica",
        "explanation_pt": "O IVA de {vat} sobre uma base de {base} corresponde a uma taxa de {taxa_calculada}, diferente da taxa {taxa_esperada} esperada para a região fiscal {regiao}.",
        "cause_pt": "Pode indicar uma taxa regional diferente da assumida, um regime especial de IVA, ou um erro de leitura do código QR.",
        "action_pt": "Confirme a taxa de IVA aplicada no documento original.",
    },
    "NIF_EMITENTE_INVALIDO": {
        "code": "NIF_EMITENTE_INVALIDO",
        "severity": "erro",
        "label_pt": "NIF do emitente inválido",
        "explanation_pt": "O NIF do emitente ({nif}) é inválido ou está ausente do documento.",
        "cause_pt": "Geralmente resulta de um erro de leitura do código QR ou de um NIF mal preenchido na fatura original.",
        "action_pt": "Confirme o NIF do emitente diretamente no documento antes de o registar.",
    },
    "NIF_ADQUIRENTE_INVALIDO": {
        "code": "NIF_ADQUIRENTE_INVALIDO",
        "severity": "aviso",
        "label_pt": "NIF do adquirente inválido",
        "explanation_pt": "O NIF do adquirente ({nif}) não passa a validação de dígito de controlo.",
        "cause_pt": "Costuma acontecer por erro de leitura do código QR ou porque o NIF foi introduzido incorretamente no ato da compra.",
        "action_pt": "Confirme o NIF do adquirente com o cliente antes de o corrigir.",
    },
    "ATCUD_AUSENTE": {
        "code": "ATCUD_AUSENTE",
        "severity": "aviso",
        "label_pt": "ATCUD em falta",
        "explanation_pt": "O documento não contém um código ATCUD (campo H).",
        "cause_pt": "Pode indicar um documento emitido antes da obrigatoriedade do ATCUD, ou um erro de leitura do código QR.",
        "action_pt": "Verifique a data do documento e, se for recente, confirme o ATCUD no original.",
    },
    "DATA_FUTURA": {
        "code": "DATA_FUTURA",
        "severity": "aviso",
        "label_pt": "Data do documento futura",
        "explanation_pt": "A data indicada no documento ({data}) é posterior à data de hoje.",
        "cause_pt": "Costuma dever-se a um erro de leitura do campo de data ou a um erro de preenchimento na fatura original.",
        "action_pt": "Confirme a data no documento original antes de o aceitar.",
    },
    "SEM_QR": {
        "code": "SEM_QR",
        "severity": "erro",
        "label_pt": "Sem código QR detetado",
        "explanation_pt": "Não foi encontrado qualquer código QR na imagem, mesmo após {tentativas} tentativas com diferentes técnicas de deteção.",
        "cause_pt": "Normalmente significa que o documento não tem código QR, ou que este está fora da imagem ou completamente ilegível.",
        "action_pt": "Confirme se o documento tem mesmo um código QR e volte a fotografá-lo se necessário.",
    },
    "DUPLICADO": {
        "code": "DUPLICADO",
        "severity": "info",
        "label_pt": "Documento possivelmente duplicado",
        "explanation_pt": "Este ATCUD e este total já tinham sido registados no documento #{original_id}.",
        "cause_pt": "Costuma acontecer quando o mesmo documento é fotografado ou carregado mais do que uma vez.",
        "action_pt": "Confirme que não está a contabilizar este documento duas vezes.",
    },
    "CONSUMIDOR_FINAL": {
        "code": "CONSUMIDOR_FINAL",
        "severity": "info",
        "label_pt": "Venda a consumidor final",
        "explanation_pt": "O NIF do adquirente é o genérico de consumidor final (999999990), pelo que não identifica um cliente específico.",
        "cause_pt": "É o comportamento normal quando o cliente não pede a inclusão do seu NIF na fatura.",
        "action_pt": "Nenhuma ação necessária - isto é esperado em vendas a particulares.",
    },
    "FORNECEDOR_DESCONHECIDO": {
        "code": "FORNECEDOR_DESCONHECIDO",
        "severity": "aviso",
        "label_pt": "Fornecedor não identificado",
        "explanation_pt": "Não foi possível identificar o nome do fornecedor com o NIF {nif} através do VIES.",
        "cause_pt": "Pode dever-se ao fornecedor não estar registado para transações intracomunitárias no VIES, ou a uma falha temporária na consulta.",
        "action_pt": "Verifique o nome do fornecedor diretamente no documento original.",
    },
    "RETENCAO_PRESENTE": {
        "code": "RETENCAO_PRESENTE",
        "severity": "info",
        "label_pt": "Retenção na fonte presente",
        "explanation_pt": "Este documento indica uma retenção na fonte de {valor}.",
        "cause_pt": "É comum em serviços prestados por profissionais independentes sujeitos a retenção de IRS ou IRC.",
        "action_pt": "Confirme que esta retenção é considerada na declaração de imposto correspondente.",
    },
    "REGIAO_FISCAL": {
        "code": "REGIAO_FISCAL",
        "severity": "info",
        "label_pt": "Região fiscal não continental",
        "explanation_pt": "Este documento indica a região fiscal {regiao}, que pode ter taxas de IVA diferentes das do Continente.",
        "cause_pt": "Acontece quando o emitente está sediado na Madeira ou nos Açores, que têm taxas de IVA reduzidas.",
        "action_pt": "Nenhuma ação necessária - confirme apenas que as taxas usadas correspondem à região indicada.",
    },
}


def format_eur_pt(value: float) -> str:
    """1234.5 -> '1.234,50 €' (PT-PT: '.' thousands, ',' decimals)."""
    text = f"{value:,.2f}"
    text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{text} €"


def format_pct_pt(rate: float) -> str:
    """0.23 -> '23%'."""
    return f"{round(rate * 100)}%"


def format_date_pt(yyyymmdd: str) -> str:
    try:
        parsed = datetime.strptime(yyyymmdd, "%Y%m%d")
    except ValueError:
        return yyyymmdd
    return parsed.strftime("%d/%m/%Y")


def render_message(code: str, detail: dict) -> str:
    template = FLAGS[code]["explanation_pt"]
    try:
        return template.format(**detail)
    except (KeyError, IndexError):
        return template


def highest_severity(flag_entries: list[dict]) -> str | None:
    if not flag_entries:
        return None
    return max(
        (FLAGS[entry["code"]]["severity"] for entry in flag_entries if entry["code"] in FLAGS),
        key=lambda sev: _SEVERITY_RANK[sev],
        default=None,
    )


def _flag(code: str, detail: dict) -> dict:
    return {"code": code, "detail": detail}


def evaluate_flags(
    fields: dict[str, str],
    *,
    decode_success: bool,
    qr_pattern_detected: bool,
    max_decode_attempts: int,
    dedup_match: dict[str, str] | None = None,
) -> list[dict]:
    """Pure decision function: given a parsed QR payload (or none, if decode
    failed) plus decode/dedup context, returns the list of {code, detail}
    flags that apply. Takes plain data only - no DB access - so every flag
    except FORNECEDOR_DESCONHECIDO can be tested without a database.
    """
    entries: list[dict] = []

    if not decode_success:
        detail = {"tentativas": max_decode_attempts}
        if qr_pattern_detected:
            entries.append(_flag("QR_ILEGIVEL", detail))
        else:
            entries.append(_flag("SEM_QR", detail))
        return entries

    if fields.get("E") == "A":
        entries.append(_flag("DOCUMENTO_ANULADO", {}))

    emitter_nif = fields.get("A")
    if not emitter_nif or not nif_checksum(emitter_nif):
        entries.append(_flag("NIF_EMITENTE_INVALIDO", {"nif": emitter_nif or "ausente"}))

    acquirer_nif = fields.get("B")
    if acquirer_nif == CONSUMIDOR_FINAL_NIF:
        entries.append(_flag("CONSUMIDOR_FINAL", {}))
    elif acquirer_nif and not nif_checksum(acquirer_nif):
        entries.append(_flag("NIF_ADQUIRENTE_INVALIDO", {"nif": acquirer_nif}))

    doc_date = fields.get("F")
    if doc_date:
        try:
            doc_date_obj = datetime.strptime(doc_date, "%Y%m%d").date()
        except ValueError:
            doc_date_obj = None
        if doc_date_obj is not None and doc_date_obj > date.today():
            entries.append(_flag("DATA_FUTURA", {"data": format_date_pt(doc_date)}))

    if not fields.get("H"):
        entries.append(_flag("ATCUD_AUSENTE", {}))

    withholding = parse_amount(fields.get("P"))
    if withholding is not None:
        entries.append(_flag("RETENCAO_PRESENTE", {"valor": format_eur_pt(withholding)}))

    tax_region = fields.get("I1")
    if tax_region and tax_region != "PT":
        entries.append(_flag("REGIAO_FISCAL", {"regiao": REGION_NAMES.get(tax_region, tax_region)}))

    bases = [parse_amount(fields.get(k)) for k in BASE_KEYS]
    vats = [parse_amount(fields.get(k)) for k in VAT_KEYS]
    bases_present = [b for b in bases if b is not None]
    vats_present = [v for v in vats if v is not None]
    total_tax = parse_amount(fields.get("N"))
    doc_total = parse_amount(fields.get("O"))

    if total_tax is not None and vats_present and abs(sum(vats_present) - total_tax) > TOLERANCE_EUR:
        entries.append(
            _flag(
                "IVA_INCONSISTENTE",
                {
                    "soma_iva": format_eur_pt(sum(vats_present)),
                    "total_imposto": format_eur_pt(total_tax),
                },
            )
        )

    if doc_total is not None and total_tax is not None and (bases_present or vats_present):
        soma = sum(bases_present) + total_tax
        if abs(soma - doc_total) > TOLERANCE_EUR:
            entries.append(
                _flag(
                    "TOTAIS_INCONSISTENTES",
                    {
                        "bases": format_eur_pt(sum(bases_present)),
                        "imposto": format_eur_pt(total_tax),
                        "soma": format_eur_pt(soma),
                        "total": format_eur_pt(doc_total),
                    },
                )
            )

    rates = rates_for_region(tax_region)
    region_label = REGION_NAMES.get(tax_region or "PT", tax_region or "PT")
    for base_key, vat_key, rate_name in RATE_BRACKETS:
        base = parse_amount(fields.get(base_key))
        vat = parse_amount(fields.get(vat_key))
        if base is not None and vat is not None:
            expected_vat = round(base * rates[rate_name], 2)
            if abs(vat - expected_vat) > TOLERANCE_EUR:
                calculated_rate = (vat / base) if base else 0.0
                entries.append(
                    _flag(
                        "TAXA_IVA_ATIPICA",
                        {
                            "vat": format_eur_pt(vat),
                            "base": format_eur_pt(base),
                            "taxa_calculada": format_pct_pt(calculated_rate),
                            "taxa_esperada": format_pct_pt(rates[rate_name]),
                            "regiao": region_label,
                        },
                    )
                )

    atcud = fields.get("H")
    if atcud and dedup_match is not None:
        existing_total = dedup_match.get("field_o")
        if amounts_equal(existing_total, fields.get("O")):
            entries.append(_flag("DUPLICADO", {"original_id": dedup_match["id"], "atcud": atcud}))
        else:
            entries.append(
                _flag(
                    "DUPLICADO_CONFLITUOSO",
                    {
                        "total_existente": format_eur_pt(parse_amount(existing_total) or 0.0),
                        "total_atual": format_eur_pt(parse_amount(fields.get("O")) or 0.0),
                    },
                )
            )

    return entries


# Supplier-lookup outcomes that mean "we tried (or can't try) and don't
# know the name" - as opposed to genuinely still pending, or a name we do
# have (found / found_no_name still counts as a known, registered trader).
_UNKNOWN_SUPPLIER_STATUSES = {"not_registered", "error", "disabled"}


def evaluate_supplier_flag(field_a: str | None, supplier_status: str | None) -> dict | None:
    """FORNECEDOR_DESCONHECIDO can't be decided inside evaluate_flags() -
    the VIES lookup it depends on runs asynchronously after the row is
    inserted (see qr_bench/suppliers.py), so this is called separately at
    read time once `supplier_status` is available (or still None/pending).
    """
    if not field_a:
        return None
    if supplier_status not in _UNKNOWN_SUPPLIER_STATUSES:
        return None
    return _flag("FORNECEDOR_DESCONHECIDO", {"nif": field_a})


# Severities that route a document to a_rever instead of straight to
# extraido - "info" flags (DOCUMENTO_ANULADO, CONSUMIDOR_FINAL, etc) are
# just observations, not reasons to hold a document for manual review.
_REVIEW_SEVERITIES = {"erro", "aviso"}


def initial_status(decode_success: bool, flag_entries: list[dict]) -> str:
    """The document lifecycle status a freshly-ingested row should start
    at: 'recebido' if nothing could even be extracted, 'a_rever' if
    extraction succeeded but something needs a human look, else
    'extraido'. Pure function of decode success + flag severities - the
    supplier-lookup-dependent FORNECEDOR_DESCONHECIDO flag is irrelevant
    here since it can't exist yet at ingestion time anyway.
    """
    if not decode_success:
        return "recebido"
    sev = highest_severity(flag_entries)
    if sev in _REVIEW_SEVERITIES:
        return "a_rever"
    return "extraido"

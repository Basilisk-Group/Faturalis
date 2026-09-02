"""Generates synthetic PT fiscal QR test images with known ground truth.

Builds valid QR payloads (correct NIF checksums, consistent tax
arithmetic), renders them with `qrcode`, pastes them onto an
invoice-like canvas, then degrades a subset realistically (blur,
rotation, JPEG artifacts, low contrast, glare) so the decode pipeline
has a real spread of difficulty to measure against.

Run directly to populate ./samples + samples/ground_truth.json:
    uv run python scripts/generate_samples.py

Also importable by tests for a hermetic, tmp_path-based dataset.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qr_bench import config  # noqa: E402
from qr_bench.validate import RATE_BRACKETS, rates_for_region  # noqa: E402

DOC_TYPES = ["FT", "FR", "FS", "NC"]


def _nif_check_digit(first8: list[int]) -> int:
    weighted_sum = sum(d * w for d, w in zip(first8, range(9, 1, -1)))
    remainder = weighted_sum % 11
    return 0 if remainder < 2 else 11 - remainder


def random_nif(rng: random.Random) -> str:
    first8 = [rng.choice([1, 2, 5, 6, 8, 9])] + [rng.randint(0, 9) for _ in range(7)]
    digits = first8 + [_nif_check_digit(first8)]
    return "".join(map(str, digits))


def _fmt_amount(x: float) -> str:
    return f"{x:.2f}"


def _random_date(rng: random.Random) -> str:
    year = rng.choice([2023, 2024, 2025])
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return f"{year:04d}{month:02d}{day:02d}"


def _future_date(rng: random.Random) -> str:
    return (datetime.now() + timedelta(days=rng.randint(10, 60))).strftime("%Y%m%d")


def _invalidate_nif(nif: str) -> str:
    """Flips the check digit of an otherwise-valid NIF so it fails checksum."""
    last = int(nif[-1])
    return nif[:-1] + str((last + 1) % 10)


@dataclass
class Payload:
    fields: dict[str, str]
    raw: str


def build_qr_payload(
    rng: random.Random,
    break_totals: bool = False,
    break_tax: bool = False,
    omit_acquirer: bool = False,
    acquirer_nif: str | None = None,
    invalid_emitter_nif: bool = False,
    invalid_acquirer_nif: bool = False,
    status: str = "N",
    atcud: str | None = None,
    omit_atcud: bool = False,
    tax_region: str = "PT",
    atypical_rate_bracket: str | None = None,
    withholding: str | None = None,
    future_date: bool = False,
) -> Payload:
    emitter_nif = random_nif(rng)
    if invalid_emitter_nif:
        emitter_nif = _invalidate_nif(emitter_nif)
    doc_type = rng.choice(DOC_TYPES)

    rates = rates_for_region(tax_region)
    if atypical_rate_bracket:
        # Force exactly the requested bracket so the atypical rate isn't
        # diluted/hidden among other, correctly-rated brackets.
        chosen_brackets = [b for b in RATE_BRACKETS if b[2] == atypical_rate_bracket]
    else:
        n_rates = rng.choice([1, 1, 2, 2, 3])
        chosen_brackets = rng.sample(RATE_BRACKETS, k=n_rates)

    tax_fields: dict[str, str] = {}
    bases_total = 0.0
    vat_total = 0.0
    for base_key, vat_key, rate_name in chosen_brackets:
        rate = rates[rate_name]
        if atypical_rate_bracket and rate_name == atypical_rate_bracket:
            rate = rate + 0.05  # deliberately wrong, in isolation
        base = round(rng.uniform(5, 200), 2)
        vat = round(base * rate, 2)
        tax_fields[base_key] = _fmt_amount(base)
        tax_fields[vat_key] = _fmt_amount(vat)
        bases_total += base
        vat_total += vat

    if not atypical_rate_bracket and rng.random() < 0.3:
        exempt = round(rng.uniform(1, 50), 2)
        tax_fields["I2"] = _fmt_amount(exempt)
        bases_total += exempt

    total_tax = round(vat_total, 2)
    if break_tax:
        total_tax = round(total_tax + rng.uniform(5, 20), 2)
    doc_total = round(bases_total + total_tax, 2)
    if break_totals:
        doc_total = round(doc_total + rng.uniform(5, 20), 2)

    ordered: dict[str, str] = {"A": emitter_nif}
    if acquirer_nif is not None:
        ordered["B"] = _invalidate_nif(acquirer_nif) if invalid_acquirer_nif else acquirer_nif
    elif invalid_acquirer_nif:
        ordered["B"] = _invalidate_nif(random_nif(rng))
    elif not omit_acquirer:
        ordered["B"] = random_nif(rng)
    ordered["C"] = "PT"
    ordered["D"] = doc_type
    ordered["E"] = status
    ordered["F"] = _future_date(rng) if future_date else _random_date(rng)
    ordered["G"] = f"{doc_type} {rng.choice(['A', 'B', '1'])}/{rng.randint(1, 9999)}"
    if not omit_atcud:
        ordered["H"] = atcud or f"{rng.randint(10000000, 99999999)}-{rng.randint(1, 999)}"
    ordered["I1"] = tax_region
    ordered.update(tax_fields)
    ordered["N"] = _fmt_amount(total_tax)
    ordered["O"] = _fmt_amount(doc_total)
    if withholding is not None:
        ordered["P"] = withholding
    ordered["Q"] = "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=4))
    ordered["R"] = str(rng.randint(1000, 9999))

    raw = "*".join(f"{k}:{v}" for k, v in ordered.items())
    return Payload(fields=ordered, raw=raw)


def render_invoice_canvas(raw: str, rng: random.Random) -> Image.Image:
    """Renders the QR onto a plain white invoice-like canvas."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=3)
    qr.add_data(raw)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    canvas_w, canvas_h = 480, 620
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(canvas)

    for i in range(6):
        y = 30 + i * 14
        draw.line([(30, y), (30 + rng.randint(120, 300), y)], fill=(60, 60, 60), width=3)

    qr_x = (canvas_w - qr_img.width) // 2
    qr_y = 220
    canvas.paste(qr_img, (qr_x, qr_y))
    return canvas


def render_blank_invoice_canvas(rng: random.Random) -> Image.Image:
    """A plain invoice-like canvas with NO QR code at all (negative control)."""
    canvas_w, canvas_h = 480, 620
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(canvas)
    for i in range(14):
        y = 30 + i * 18
        draw.line([(30, y), (30 + rng.randint(100, 350), y)], fill=(60, 60, 60), width=3)
    return canvas


def corrupt_qr_data_region(raw: str) -> Image.Image:
    """Builds a QR code, then overwrites its interior data modules with
    noise while leaving the three finder-pattern corners intact (they sit
    within the outer ~28% margin on each side). A detector can therefore
    still locate a QR-shaped region (qr_pattern_detected=True), but
    decoding the corrupted data reliably fails - a deterministic way to
    trigger QR_ILEGIVEL, unlike blur/JPEG fuzziness which only sometimes
    breaks decoding. Verified empirically across 10+ payload lengths.
    """
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=3)
    qr.add_data(raw)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    arr = np.array(img)
    h, w, _ = arr.shape
    margin = int(min(h, w) * 0.28)
    noise_rng = np.random.default_rng(12345)
    region_h, region_w = h - 2 * margin, w - 2 * margin
    noise = noise_rng.integers(0, 256, size=(region_h, region_w, 3), dtype=np.uint8)
    arr[margin : margin + region_h, margin : margin + region_w] = noise
    return Image.fromarray(arr)


def degrade_blur(image: Image.Image, radius: float) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius))


def degrade_rotate(image: Image.Image, angle: float) -> Image.Image:
    return image.rotate(angle, expand=True, fillcolor="white")


def degrade_jpeg(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def degrade_low_contrast(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Contrast(image).enhance(factor)


def degrade_glare(image: Image.Image, rng: random.Random) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = image.size
    cx, cy = rng.randint(w // 4, 3 * w // 4), rng.randint(h // 4, 3 * h // 4)
    r = rng.randint(min(w, h) // 6, min(w, h) // 3)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 150))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


DEGRADE_FNS = {
    "blur": degrade_blur,
    "rotate": degrade_rotate,
    "jpeg": degrade_jpeg,
    "low_contrast": degrade_low_contrast,
    "glare": degrade_glare,
}


@dataclass
class SampleRecord:
    filename: str
    tier: str
    degradations: list[str]
    expected_decodable: bool
    expected_no_qr: bool
    expected_fields: dict[str, str] = field(default_factory=dict)
    raw_qr: str | None = None


def _apply_tier(image: Image.Image, tier: str, rng: random.Random) -> tuple[Image.Image, list[str]]:
    if tier == "clean":
        return image, []
    if tier == "moderate":
        choice = rng.choice(["blur_mild", "jpeg_mild", "contrast_mild", "rotate_exact"])
        if choice == "blur_mild":
            return degrade_blur(image, rng.uniform(0.8, 1.6)), ["blur"]
        if choice == "jpeg_mild":
            return degrade_jpeg(image, rng.randint(35, 55)), ["jpeg"]
        if choice == "contrast_mild":
            return degrade_low_contrast(image, rng.uniform(0.4, 0.6)), ["low_contrast"]
        angle = rng.choice([90, 180, 270])
        return degrade_rotate(image, angle), [f"rotate{angle}"]
    if tier == "heavy":
        img = degrade_blur(image, rng.uniform(1.5, 2.5))
        img = degrade_low_contrast(img, rng.uniform(0.3, 0.45))
        img = degrade_jpeg(img, rng.randint(15, 30))
        applied = ["blur", "low_contrast", "jpeg"]
        if rng.random() < 0.5:
            img = degrade_glare(img, rng)
            applied.append("glare")
        if rng.random() < 0.4:
            skew = rng.uniform(5, 20) * rng.choice([-1, 1])
            img = degrade_rotate(img, skew)
            applied.append(f"skew{skew:.0f}")
        return img, applied
    raise ValueError(f"unknown tier: {tier}")


@dataclass
class FlagSample:
    """One rendered image (+ the payload that produced it, when there is
    one) used to deterministically trigger a specific flag code."""

    image: Image.Image
    payload: Payload | None = None


def _build_documento_anulado(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, status="A")
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_duplicado(rng: random.Random) -> list[FlagSample]:
    atcud = f"{rng.randint(10000000, 99999999)}-{rng.randint(1, 999)}"
    payload = build_qr_payload(rng, atcud=atcud)
    # The exact same image, processed twice, has identical H and O -> the
    # second pass is a true duplicate, not a conflict.
    image = render_invoice_canvas(payload.raw, rng)
    return [FlagSample(image, payload), FlagSample(image, payload)]


def _build_duplicado_conflituoso(rng: random.Random) -> list[FlagSample]:
    atcud = f"{rng.randint(10000000, 99999999)}-{rng.randint(1, 999)}"
    payload1 = build_qr_payload(rng, atcud=atcud)
    image1 = render_invoice_canvas(payload1.raw, rng)

    conflicting_total = _fmt_amount(round(float(payload1.fields["O"]) + 75.0, 2))
    raw2 = payload1.raw.replace(f"O:{payload1.fields['O']}", f"O:{conflicting_total}")
    payload2 = Payload(fields={**payload1.fields, "O": conflicting_total}, raw=raw2)
    image2 = render_invoice_canvas(raw2, rng)

    return [FlagSample(image1, payload1), FlagSample(image2, payload2)]


def _build_qr_ilegivel(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng)
    return [FlagSample(corrupt_qr_data_region(payload.raw))]


def _build_sem_qr(rng: random.Random) -> list[FlagSample]:
    return [FlagSample(render_blank_invoice_canvas(rng))]


def _build_totais_inconsistentes(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, break_totals=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_iva_inconsistente(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, break_tax=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_taxa_iva_atipica(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, atypical_rate_bracket="normal")
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_nif_emitente_invalido(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, invalid_emitter_nif=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_nif_adquirente_invalido(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, invalid_acquirer_nif=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_atcud_ausente(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, omit_atcud=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_data_futura(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, future_date=True)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_consumidor_final(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, acquirer_nif="999999990")
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_fornecedor_desconhecido(rng: random.Random) -> list[FlagSample]:
    # The image alone can't trigger this - it depends on the async VIES
    # lookup outcome, resolved separately (see tests/test_flags.py). Any
    # normal, valid document is fine as the base sample.
    payload = build_qr_payload(rng)
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_retencao_presente(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, withholding="12.34")
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


def _build_regiao_fiscal(rng: random.Random) -> list[FlagSample]:
    payload = build_qr_payload(rng, tax_region="PT-MA")
    return [FlagSample(render_invoice_canvas(payload.raw, rng), payload)]


# code -> builder. Each builder returns a list of one or more FlagSamples;
# when there's more than one, they must be processed IN ORDER (earlier ones
# set up state, e.g. an ATCUD already on record) and the flag is only
# expected to fire on the last one. Deliberately checked for completeness
# against qr_bench.flags.FLAGS by tests/test_flags.py - the whole point is
# that this dict can't silently drift out of sync with the registry.
FLAG_SAMPLE_BUILDERS: dict[str, Callable[[random.Random], list[FlagSample]]] = {
    "DOCUMENTO_ANULADO": _build_documento_anulado,
    "DUPLICADO": _build_duplicado,
    "DUPLICADO_CONFLITUOSO": _build_duplicado_conflituoso,
    "QR_ILEGIVEL": _build_qr_ilegivel,
    "SEM_QR": _build_sem_qr,
    "TOTAIS_INCONSISTENTES": _build_totais_inconsistentes,
    "IVA_INCONSISTENTE": _build_iva_inconsistente,
    "TAXA_IVA_ATIPICA": _build_taxa_iva_atipica,
    "NIF_EMITENTE_INVALIDO": _build_nif_emitente_invalido,
    "NIF_ADQUIRENTE_INVALIDO": _build_nif_adquirente_invalido,
    "ATCUD_AUSENTE": _build_atcud_ausente,
    "DATA_FUTURA": _build_data_futura,
    "CONSUMIDOR_FINAL": _build_consumidor_final,
    "FORNECEDOR_DESCONHECIDO": _build_fornecedor_desconhecido,
    "RETENCAO_PRESENTE": _build_retencao_presente,
    "REGIAO_FISCAL": _build_regiao_fiscal,
}


def generate_special_samples(output_dir: Path, rng: random.Random, start_idx: int) -> list[SampleRecord]:
    """Renders clean (undegraded) samples that exercise specific dashboard
    features rather than decode difficulty: a cancelled document, a
    duplicate ATCUD pair, a conflicting duplicate (same ATCUD, different
    total), and a Madeira (PT-MA) document with region-correct VAT rates.
    """
    records: list[SampleRecord] = []
    idx = start_idx

    idx += 1
    cancelled_payload = build_qr_payload(rng, status="A")
    filename = f"sample_{idx:02d}_cancelled.png"
    render_invoice_canvas(cancelled_payload.raw, rng).save(output_dir / filename)
    records.append(
        SampleRecord(
            filename=filename,
            tier="cancelled",
            degradations=[],
            expected_decodable=True,
            expected_no_qr=False,
            expected_fields=cancelled_payload.fields,
            raw_qr=cancelled_payload.raw,
        )
    )

    dup_atcud = f"{rng.randint(10000000, 99999999)}-{rng.randint(1, 999)}"
    dup_payload = build_qr_payload(rng, atcud=dup_atcud)
    dup_image = render_invoice_canvas(dup_payload.raw, rng)
    for suffix in ("a", "b"):
        idx += 1
        filename = f"sample_{idx:02d}_duplicate_{suffix}.png"
        dup_image.save(output_dir / filename)
        records.append(
            SampleRecord(
                filename=filename,
                tier="duplicate",
                degradations=[],
                expected_decodable=True,
                expected_no_qr=False,
                expected_fields=dup_payload.fields,
                raw_qr=dup_payload.raw,
            )
        )

    conflict_atcud = f"{rng.randint(10000000, 99999999)}-{rng.randint(1, 999)}"
    base_payload = build_qr_payload(rng, atcud=conflict_atcud)
    idx += 1
    filename_a = f"sample_{idx:02d}_conflict_a.png"
    render_invoice_canvas(base_payload.raw, rng).save(output_dir / filename_a)
    records.append(
        SampleRecord(
            filename=filename_a,
            tier="conflict",
            degradations=[],
            expected_decodable=True,
            expected_no_qr=False,
            expected_fields=base_payload.fields,
            raw_qr=base_payload.raw,
        )
    )

    conflicting_total = _fmt_amount(round(float(base_payload.fields["O"]) + 75.00, 2))
    conflicting_raw = base_payload.raw.replace(f"O:{base_payload.fields['O']}", f"O:{conflicting_total}")
    conflicting_fields = {**base_payload.fields, "O": conflicting_total}
    idx += 1
    filename_b = f"sample_{idx:02d}_conflict_b.png"
    render_invoice_canvas(conflicting_raw, rng).save(output_dir / filename_b)
    records.append(
        SampleRecord(
            filename=filename_b,
            tier="conflict",
            degradations=[],
            expected_decodable=True,
            expected_no_qr=False,
            expected_fields=conflicting_fields,
            raw_qr=conflicting_raw,
        )
    )

    idx += 1
    ma_payload = build_qr_payload(rng, tax_region="PT-MA")
    filename = f"sample_{idx:02d}_pt_ma.png"
    render_invoice_canvas(ma_payload.raw, rng).save(output_dir / filename)
    records.append(
        SampleRecord(
            filename=filename,
            tier="pt_ma",
            degradations=[],
            expected_decodable=True,
            expected_no_qr=False,
            expected_fields=ma_payload.fields,
            raw_qr=ma_payload.raw,
        )
    )

    return records


def generate_dataset(
    output_dir: Path,
    count: int = 40,
    seed: int = 42,
    clean_n: int = 8,
    moderate_n: int = 17,
    heavy_n: int = 10,
    no_qr_n: int = 5,
    include_special: bool = True,
) -> list[SampleRecord]:
    assert clean_n + moderate_n + heavy_n + no_qr_n == count
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    records: list[SampleRecord] = []
    idx = 0

    plan = (
        [("clean", False) for _ in range(clean_n)]
        + [("moderate", i < 2) for i in range(moderate_n)]
        + [("heavy", False) for _ in range(heavy_n)]
    )
    rng.shuffle(plan)

    for tier, break_totals in plan:
        idx += 1
        payload = build_qr_payload(rng, break_totals=break_totals)
        canvas = render_invoice_canvas(payload.raw, rng)
        degraded, applied = _apply_tier(canvas, tier, rng)

        ext = ".jpg" if "jpeg" in applied else ".png"
        filename = f"sample_{idx:02d}_{tier}{ext}"
        path = output_dir / filename
        degraded.save(path)

        records.append(
            SampleRecord(
                filename=filename,
                tier=tier,
                degradations=applied,
                expected_decodable=(tier != "heavy"),
                expected_no_qr=False,
                expected_fields=payload.fields,
                raw_qr=payload.raw,
            )
        )

    for _ in range(no_qr_n):
        idx += 1
        canvas = render_blank_invoice_canvas(rng)
        filename = f"sample_{idx:02d}_no_qr.png"
        path = output_dir / filename
        canvas.save(path)
        records.append(
            SampleRecord(
                filename=filename,
                tier="no_qr",
                degradations=[],
                expected_decodable=False,
                expected_no_qr=True,
            )
        )

    if include_special:
        records.extend(generate_special_samples(output_dir, rng, start_idx=idx))

    ground_truth_path = output_dir / "ground_truth.json"
    ground_truth_path.write_text(
        json.dumps([r.__dict__ for r in records], indent=2)
    )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=config.SAMPLES_DIR)
    args = parser.parse_args()

    records = generate_dataset(args.out, count=args.count, seed=args.seed)
    print(f"generated {len(records)} samples in {args.out}")


if __name__ == "__main__":
    main()

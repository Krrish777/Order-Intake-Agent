"""Generate GLFP's X12 850 — Phase 4.2 minimalist variant.

GLFP's EDI traffic is structurally legal X12 but deliberately sparse — they
omit optional segments that Patterson's implementation guide includes. The
agent has to resolve line items using only GLFP's 6-digit prefixed part
numbers in PO107 since the usual fallbacks (free-form PID descriptions,
quoted unit prices) simply aren't present.

Where GLFP differs from Patterson's full envelope (Phase 4.1):

1.  **ISA qualifier `ZZ`** (mutually defined) and **ISA ID `GLFPMI01`** —
    not a D-U-N-S-based scheme. Per customer master.
2.  **No PID segments** — descriptions are the partner's problem. Agent
    must resolve GLFP-XXXXX aliases via `customers.json` alone.
3.  **No PO104 unit price / PO105 basis** — three consecutive empty elements
    (`EA***BP`) following the UOM. The extractor must preserve empty-field
    positioning when parsing PO1.
4.  **Single N1 loop** — ship-to only, no bill-to. GLFP's trading partner
    agreement defaults bill-to to main warehouse unless a different location
    is named explicitly.
5.  **Eight lines**, all hydraulic + one pneumatic, using GLFP prefixed
    aliases from the master (GLFP-04821, GLFP-05110, etc.).
6.  **Same segment terminator `~`** — the minimalism is in omitted segments,
    not exotic separator choices. Separator roulette tempts realism-seekers
    but is rare in real mid-market EDI (partners prefer defaults).
7.  **GS08 = 004010** — same X12 version as Patterson. Version drift is
    uncommon; the structural minimalism is the story here.

Run with: ``uv run python -m scripts.generators.edi_glfp_850``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# --- Separators (same defaults as Patterson; minimalism is elsewhere) ---
ELEM = "*"
COMP = ":"
REPS = "^"
SEG = "~"
NL = "\n"

# --- Envelope identities (from customers.json) ---
SENDER_QUAL = "ZZ"
SENDER_ID = "GLFPMI01"
SENDER_AS2 = "GLFP-MICH"

RECEIVER_QUAL = "ZZ"
RECEIVER_ID = "GRAFTON-REESE"

# --- Transaction metadata (from filename glfp_850_GRR_202604211002.edi) ---
INTERCHANGE_DATE = "260421"
INTERCHANGE_TIME = "1002"
ISO_DATE = "20260421"
ICN = "000001152"
GCN = "1152"
TCN = "0001"

PO_NBR = "PO-2026-05849"
DELIV_BY = "20260428"  # GLFP's one-week typical turnaround on stocked hydraulic


@dataclass(frozen=True)
class Line:
    glfp_part: str
    vendor_pn: str
    qty: int
    uom: str


# Eight lines — GLFP prefixed aliases only. No unit prices (PO104 empty).
LINES: tuple[Line, ...] = (
    Line("GLFP-04821", "HYD-MJS-06-STL",       36, "EA"),
    Line("GLFP-04824", "HYD-MJE-06-STL",       18, "EA"),
    Line("GLFP-04831", "HYD-MJN-06-04-STL",    24, "EA"),
    Line("GLFP-05110", "HYD-HSE-R2-06",       200, "FT"),
    Line("GLFP-05115", "HYD-HSE-R2-08",       150, "FT"),
    Line("GLFP-05220", "HYD-HFF-08-STL",       40, "EA"),
    Line("GLFP-06010", "HYD-QCA-06-STL",        5, "EA"),
    Line("GLFP-11200", "PNM-QCI-025N-STL",     30, "EA"),
)


def _pad(value: str, width: int) -> str:
    if len(value) > width:
        raise ValueError(f"{value!r} longer than {width}")
    return value.ljust(width, " ")


def _segment(*elements: str) -> str:
    return ELEM.join(elements) + SEG


def build_transaction() -> list[str]:
    segs: list[str] = []
    segs.append(_segment("ST", "850", TCN))
    segs.append(_segment("BEG", "00", "SA", PO_NBR, "", ISO_DATE))
    segs.append(_segment("REF", "CO", "GLFP-WK17"))
    segs.append(_segment("DTM", "002", DELIV_BY))

    # Single N1 loop — ship-to only
    segs.append(_segment("N1", "ST", "GLFP MAIN WAREHOUSE", "92", "GLFP-GRR-MAIN"))
    segs.append(_segment("N3", "1847 LEONARD STREET NW", "RECEIVING BLDG B"))
    segs.append(_segment("N4", "GRAND RAPIDS", "MI", "49504"))

    # PO1 lines — no PO104 (price), no PO105 (basis), no PID after
    for i, ln in enumerate(LINES, start=1):
        segs.append(_segment(
            "PO1",
            f"{i:03d}",
            str(ln.qty),
            ln.uom,
            "",              # PO104 unit price (intentionally empty)
            "",              # PO105 basis (intentionally empty)
            "BP", ln.glfp_part,
            "VP", ln.vendor_pn,
        ))

    segs.append(_segment("CTT", str(len(LINES))))
    seg_count = len(segs) + 1
    segs.append(_segment("SE", str(seg_count), TCN))
    return segs


def build_edi() -> str:
    st_segs = build_transaction()

    isa = _segment(
        "ISA",
        "00", _pad("", 10),
        "00", _pad("", 10),
        SENDER_QUAL, _pad(SENDER_ID, 15),
        RECEIVER_QUAL, _pad(RECEIVER_ID, 15),
        INTERCHANGE_DATE, INTERCHANGE_TIME,
        REPS, "00401", ICN, "0", "P", COMP,
    )
    gs = _segment(
        "GS", "PO", SENDER_AS2, RECEIVER_ID,
        ISO_DATE, INTERCHANGE_TIME, GCN, "X", "004010",
    )
    ge = _segment("GE", "1", GCN)
    iea = _segment("IEA", "1", ICN)

    return NL.join([isa, gs] + st_segs + [ge, iea]) + NL


def verify(text: str) -> None:
    lines = [ln for ln in text.split(NL) if ln]

    assert lines[0].startswith("ISA*")
    assert lines[-1].startswith("IEA*")

    # SE01 must equal actual ST..SE span
    st_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ST*"))
    se_idx = next(i for i, ln in enumerate(lines) if ln.startswith("SE*"))
    actual = se_idx - st_idx + 1
    declared = int(lines[se_idx].split(ELEM)[1])
    assert declared == actual, f"SE01={declared} actual={actual}"

    # CTT*N == PO1 count
    po1s = sum(1 for ln in lines if ln.startswith("PO1*"))
    ctt_n = int(next(ln for ln in lines if ln.startswith("CTT*")).split(ELEM)[1].rstrip(SEG))
    assert ctt_n == po1s

    # Minimalism assertions — the things that make this file distinct
    assert not any(ln.startswith("PID*") for ln in lines), "PID segments must be absent"
    assert not any(ln.startswith("N1*BT") for ln in lines), "N1*BT (bill-to) must be absent"

    # Every PO1 line must have empty PO104 and PO105
    for ln in (l for l in lines if l.startswith("PO1*")):
        parts = ln.rstrip(SEG).split(ELEM)
        # parts: PO1, 001, 36, EA, '', '', BP, ..., VP, ...
        assert parts[4] == "" and parts[5] == "", f"PO104/PO105 not empty in {ln}"
        assert parts[6] == "BP", f"PO106 must be BP qualifier in {ln}"

    # Control number consistency
    isa_icn = lines[0].split(ELEM)[13]
    iea_icn = lines[-1].split(ELEM)[2].rstrip(SEG)
    assert isa_icn == iea_icn

    # ISA fixed widths
    isa_parts = lines[0].split(ELEM)
    assert len(isa_parts[6]) == 15 and len(isa_parts[8]) == 15


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "edi" / "glfp_850_GRR_202604211002.edi"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = build_edi()
    verify(text)
    out.write_text(text, encoding="ascii", newline="")
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")

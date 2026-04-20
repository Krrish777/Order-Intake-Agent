"""Generate Patterson's X12 850 — Phase 4.1 full-envelope exemplar.

Patterson routes POs through OpenText GXS VAN using X12 4010. This is the full
shape: ISA/IEA interchange envelope, GS/GE functional group, ST/SE transaction
set, both BT and ST N1 loops, PO1 line items paired with PID free-form
descriptions, DTM date codes 002 and 010, and a CTT trailer. Every envelope
counter and segment count is computed programmatically — an EDI translator
that rejects a mismatched SE01 is the point of the assertion pass at the end.

Realism anchors:

1.  **Separators** — standard industry defaults per the ISA16 component
    separator: element `*`, segment `~`, component `:`, repetition `^`.
    GS08 advertises `004010` per the X12 4010 Implementation Convention.
2.  **Patterson's AS2/ISA identity** from customer master: ISA qualifier `01`
    (D-U-N-S), ISA ID `1349821507`, AS2 ID `PATTERSON-INDUST` (used as GS02).
    Grafton-Reese as receiver: qualifier `ZZ` (mutually defined), ID
    `GRAFTON-REESE`.
3.  **Fixed-width ISA padding.** ISA06/08 are 15-char fields that must be
    space-padded (left-justified). Authorization/security fields (ISA02/04)
    are 10-char empty blanks. A real EDI translator validates these widths.
4.  **Both N1 loops** — `N1*BT` (bill-to) and `N1*ST` (ship-to). Patterson
    notes require ship-to location code on every PO; the code goes in the
    N1*ST*...*92 (location code qualifier) field.
5.  **PID per line** — free-form product description in `PID*F****<desc>`.
    Gives the extractor a human-readable backstop when Patterson's alias
    (`887712`) can't be resolved via the `sku_aliases` map.
6.  **Buyer + vendor part numbers** in PO1 via BP/VP qualifiers — the
    reverse-lookup anchor.
7.  **Contract pricing** in PO104 with basis `WE` (wholesale/each).
8.  **CTT*5** trailer confirms the 5 PO1 lines.
9.  **SE01 = segment count** from ST through SE inclusive — asserted.
10. **Interchange/group/transaction control numbers** — independent counters.
    ICN matches IEA02, GCN matches GE02, TCN matches SE02.

Run with: ``uv run python -m scripts.generators.edi_patterson_850``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# --- Separators (X12 convention) ---
ELEM = "*"
COMP = ":"
REPS = "^"
SEG = "~"
NL = "\n"   # for human readability; X12 wire format is legal either way

# --- Envelope identities ---
SENDER_QUAL = "01"
SENDER_ID = "1349821507"
SENDER_AS2 = "PATTERSON-INDUST"

RECEIVER_QUAL = "ZZ"
RECEIVER_ID = "GRAFTON-REESE"

# --- Transaction metadata (from filename patterson_850_CLE_202604191435.edi) ---
INTERCHANGE_DATE = "260419"     # YYMMDD
INTERCHANGE_TIME = "1435"
ISO_DATE = "20260419"
ICN = "000000847"   # interchange control number
GCN = "847"         # group control number
TCN = "0001"        # transaction control number

PO_NBR = "PO-28492"
DELIV_BY = "20260508"   # DTM*002
REQ_SHIP = "20260506"   # DTM*010


@dataclass(frozen=True)
class Line:
    buyer_pn: str
    vendor_pn: str
    description: str
    qty: int
    uom: str
    unit_price: float


LINES: tuple[Line, ...] = (
    Line("887712", "FST-HCS-050-13-200-G5Z", "Hex Cap Screw 1/2-13 x 2in Gr 5 Zinc",           500, "EA",  0.30),
    Line("912055", "HYD-MJS-06-STL",         "JIC Male Straight -6 9/16-18 UNF Steel ZN",       60, "EA",  2.98),
    Line("912061", "HYD-MJE-06-STL",         "JIC Male Elbow 90 -6 9/16-18 UNF Forged Steel",   24, "EA",  7.66),
    Line("912118", "HYD-HSE-R2-06",          "Hydraulic Hose 100R2 -6 3/8in ID 2-Wire",        300, "FT",  3.37),
    Line("912402", "HYD-QCA-06-STL",         "QD Coupler ISO 7241-A AG 3/8in Body 3/8 NPT-F",   10, "EA", 28.15),
)


def _pad(value: str, width: int) -> str:
    """Left-justify and space-pad to fixed X12 ISA field width."""
    if len(value) > width:
        raise ValueError(f"value {value!r} longer than field width {width}")
    return value.ljust(width, " ")


def _segment(*elements: str) -> str:
    """Join elements with the element separator and append segment terminator."""
    return ELEM.join(elements) + SEG


def build_transaction() -> tuple[list[str], int, int]:
    """Build the ST/.../SE segments. Returns (segments, seg_count, line_count)."""
    segs: list[str] = []

    # ST — transaction set header
    segs.append(_segment("ST", "850", TCN))
    # BEG — beginning: 00=original, SA=stand-alone PO
    segs.append(_segment("BEG", "00", "SA", PO_NBR, "", ISO_DATE))
    # REF — department + cross-reference
    segs.append(_segment("REF", "DP", "300"))
    segs.append(_segment("REF", "CO", "PATT-CLE-ORDER-8472"))
    # DTM — delivery required (002) + requested ship (010)
    segs.append(_segment("DTM", "002", DELIV_BY))
    segs.append(_segment("DTM", "010", REQ_SHIP))

    # N1 loop — Bill To
    segs.append(_segment("N1", "BT", "PATTERSON INDUSTRIAL SUPPLY CO", "92", "PATT-BILL"))
    segs.append(_segment("N3", "2750 HARVARD AVENUE", "ATTN A/P DEPT 300"))
    segs.append(_segment("N4", "CLEVELAND", "OH", "44105"))

    # N1 loop — Ship To (with location code in the 92 qualifier)
    segs.append(_segment("N1", "ST", "PATTERSON DC CLEVELAND", "92", "PATT-CLE-01"))
    segs.append(_segment("N3", "2750 HARVARD AVENUE", "DOCK 14"))
    segs.append(_segment("N4", "CLEVELAND", "OH", "44105"))

    # PO1 + PID per line
    for i, ln in enumerate(LINES, start=1):
        segs.append(_segment(
            "PO1",
            f"{i:03d}",
            str(ln.qty),
            ln.uom,
            f"{ln.unit_price:.2f}",
            "WE",
            "BP", ln.buyer_pn,
            "VP", ln.vendor_pn,
        ))
        segs.append(_segment("PID", "F", "", "", "", ln.description))

    # CTT — transaction totals
    segs.append(_segment("CTT", str(len(LINES))))
    # SE — segment count placeholder, fixed up below
    # We count ST..SE inclusive, so segments-so-far + 1 (for SE itself)
    seg_count = len(segs) + 1
    segs.append(_segment("SE", str(seg_count), TCN))

    return segs, seg_count, len(LINES)


def build_edi() -> str:
    st_segs, _, _ = build_transaction()

    isa = _segment(
        "ISA",
        "00",                      # I01 auth info qualifier
        _pad("", 10),              # I02 auth info
        "00",                      # I03 security qualifier
        _pad("", 10),              # I04 security info
        SENDER_QUAL,               # I05
        _pad(SENDER_ID, 15),       # I06
        RECEIVER_QUAL,             # I07
        _pad(RECEIVER_ID, 15),     # I08
        INTERCHANGE_DATE,          # I09
        INTERCHANGE_TIME,          # I10
        REPS,                      # I11 repetition separator
        "00401",                   # I12 interchange version
        ICN,                       # I13 interchange control number
        "0",                       # I14 ack requested
        "P",                       # I15 test/production
        COMP,                      # I16 component element separator
    )
    gs = _segment(
        "GS",
        "PO",                       # functional ID code
        SENDER_AS2,                 # application sender
        RECEIVER_ID,                # application receiver
        ISO_DATE,                   # date
        INTERCHANGE_TIME,           # time
        GCN,                        # group control number
        "X",                        # responsible agency code
        "004010",                   # version
    )
    ge = _segment("GE", "1", GCN)
    iea = _segment("IEA", "1", ICN)

    parts = [isa, gs] + st_segs + [ge, iea]
    return NL.join(parts) + NL


def verify(edi_text: str) -> None:
    """Programmatic assertions that must hold before we ship the file."""
    lines = [ln for ln in edi_text.split(NL) if ln]

    # Structural sanity
    assert lines[0].startswith("ISA*"), "ISA must be first segment"
    assert lines[1].startswith("GS*"), "GS must follow ISA"
    assert lines[-1].startswith("IEA*"), "IEA must be last segment"
    assert lines[-2].startswith("GE*"), "GE must precede IEA"

    # SE01 segment count must match actual ST..SE span
    st_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ST*"))
    se_idx = next(i for i, ln in enumerate(lines) if ln.startswith("SE*"))
    actual_count = se_idx - st_idx + 1
    se_declared = int(lines[se_idx].split(ELEM)[1])
    assert se_declared == actual_count, f"SE01={se_declared} but actual segments={actual_count}"

    # CTT*N must match PO1 count
    po1_count = sum(1 for ln in lines if ln.startswith("PO1*"))
    ctt_line = next(ln for ln in lines if ln.startswith("CTT*"))
    ctt_n = int(ctt_line.split(ELEM)[1].rstrip(SEG))
    assert ctt_n == po1_count, f"CTT={ctt_n} but PO1 lines={po1_count}"

    # Control numbers must match (envelope consistency)
    isa_icn = lines[0].split(ELEM)[13]
    iea_icn = lines[-1].split(ELEM)[2].rstrip(SEG)
    assert isa_icn == iea_icn, f"ISA13={isa_icn} vs IEA02={iea_icn}"
    gs_gcn = lines[1].split(ELEM)[6]
    ge_gcn = lines[-2].split(ELEM)[2].rstrip(SEG)
    assert gs_gcn == ge_gcn, f"GS06={gs_gcn} vs GE02={ge_gcn}"

    # ISA fixed-width fields
    isa_parts = lines[0].split(ELEM)
    assert len(isa_parts[6]) == 15, "ISA06 sender ID must be 15 chars"
    assert len(isa_parts[8]) == 15, "ISA08 receiver ID must be 15 chars"


def main() -> Path:
    out = Path(__file__).resolve().parents[2] / "data" / "edi" / "patterson_850_CLE_202604191435.edi"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = build_edi()
    verify(text)
    # EDI is ASCII-only per X12; write without BOM, with the generator's
    # explicit segment terminator + newline already in the text.
    out.write_text(text, encoding="ascii", newline="")
    return out


if __name__ == "__main__":
    path = main()
    print(f"wrote: {path}")

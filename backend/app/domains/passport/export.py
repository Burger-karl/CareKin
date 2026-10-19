"""One-page health passport PDF for a doctor at a new facility (HP-2).

Rules:
- Always exactly one page. The content width is computed explicitly (page width minus
  both margins), and every table's columns add up to it, so nothing is clipped at the
  edge. Entries are added newest first until the page is full; the footer says how many
  were left out.
- Everything written by users is escaped before it reaches reportlab's markup, and long
  fields are shortened, so no entry can break the layout.
- Unicode names (e.g. Igbo "Ụ", "ọ") need a font that has them. DejaVu Sans is used when
  installed (the Dockerfile installs fonts-dejavu-core). Without it, reportlab's bundled
  Vera is used and accents are dropped rather than printed as black boxes.
- Rendered inline in the request: a page takes milliseconds, and keeping it in memory
  means no copy of someone's health record is ever stored in a queue or a bucket.
"""

from __future__ import annotations

import io
import os
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LAGOS = timezone(timedelta(hours=1), "WAT")

# ---------------------------------------------------------------- page geometry
PAGE_W, PAGE_H = A4
MARGIN_X = 40.0
MARGIN_TOP = 36.0
MARGIN_BOTTOM = 54.0  # room for the footer
CONTENT_W = PAGE_W - 2 * MARGIN_X  # 515.28 pt
FRAME_H = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM
FRAME_PADDING = 6.0  # SimpleDocTemplate's frame padding, each side

INK = HexColor("#1F2A30")
MUTED = HexColor("#5F6B72")
ACCENT = HexColor("#14435A")
BAND = HexColor("#EEF3F5")
RULE = HexColor("#CBD5DA")

# Column widths always sum to CONTENT_W.
ENTRY_COLS = (CONTENT_W * 0.16, CONTENT_W * 0.15, CONTENT_W * 0.69)
PATIENT_COLS = (
    CONTENT_W * 0.36,
    CONTENT_W * 0.22,
    CONTENT_W * 0.12,
    CONTENT_W * 0.15,
    CONTENT_W * 0.15,
)
PROFILE_COLS = (CONTENT_W * 0.22, CONTENT_W * 0.78)

SUMMARY_LIMIT = 220
PROFILE_LIMIT = 260
FACILITY_LIMIT = 60

DISCLAIMER = (
    "Patient-controlled record kept in CareKin by the patient or their guardian. It is not a "
    "diagnosis, prescription or treatment plan: confirm details with the patient and your own "
    "assessment. Medicines are listed as reported, never with dosing advice."
)

TYPE_LABELS = {
    "crisis": "Crisis",
    "trigger": "Trigger",
    "visit": "Hospital visit",
    "transfusion": "Transfusion",
    "medication": "Medication",
}


# ---------------------------------------------------------------- fonts
@dataclass(frozen=True)
class Fonts:
    regular: str
    bold: str
    unicode: bool  # False: accents are stripped before drawing


_DEJAVU_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/local/share/fonts/dejavu",
    "/Library/Fonts",
    "C:/Windows/Fonts",
)


@lru_cache
def fonts() -> Fonts:
    for folder in _DEJAVU_DIRS:
        regular = os.path.join(folder, "DejaVuSans.ttf")
        bold = os.path.join(folder, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("CK-Regular", regular))
            pdfmetrics.registerFont(TTFont("CK-Bold", bold))
            return Fonts("CK-Regular", "CK-Bold", unicode=True)
    bundled = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    pdfmetrics.registerFont(TTFont("CK-Regular", os.path.join(bundled, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("CK-Bold", os.path.join(bundled, "VeraBd.ttf")))
    return Fonts("CK-Regular", "CK-Bold", unicode=False)


def _plain(text: str, unicode_font: bool) -> str:
    """Make text safe to draw: no control characters, accents dropped if the font lacks them."""
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    if unicode_font:
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("latin-1", "replace").decode("latin-1")


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------- input
@dataclass(frozen=True)
class ExportEntry:
    occurred_at: datetime
    entry_type: str
    summary: str
    facility_name: str | None = None


@dataclass(frozen=True)
class ExportInput:
    full_name: str
    date_of_birth: date
    sex: str | None
    genotype: str | None
    blood_group: str | None
    entries: Sequence[ExportEntry]  # newest first
    exported_at: datetime
    exported_for: str  # e.g. "Patient", "Guardian", "Shared with Parklane Hospital"
    known_triggers: str | None = None
    medications_overview: str | None = None
    allergies: str | None = None
    care_instructions: str | None = None
    show_profile: bool = True


@dataclass
class ExportResult:
    pdf: bytes
    pages: int
    entries_shown: int
    entries_total: int
    unicode_font: bool
    stats: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- building blocks
class _Styles:
    def __init__(self, f: Fonts) -> None:
        self.title = ParagraphStyle("t", fontName=f.bold, fontSize=16, leading=19, textColor=ACCENT)
        self.meta = ParagraphStyle(
            "m", fontName=f.regular, fontSize=7.6, leading=10, textColor=MUTED
        )
        self.h = ParagraphStyle(
            "h",
            fontName=f.bold,
            fontSize=9.2,
            leading=12,
            textColor=ACCENT,
            spaceBefore=6,
            spaceAfter=3,
        )
        self.cell = ParagraphStyle(
            "c", fontName=f.regular, fontSize=8, leading=10, textColor=INK, splitLongWords=1
        )
        self.cell_b = ParagraphStyle("cb", parent=self.cell, fontName=f.bold)
        self.label = ParagraphStyle("l", fontName=f.bold, fontSize=7, leading=9, textColor=MUTED)
        self.note = ParagraphStyle(
            "n", fontName=f.regular, fontSize=7.2, leading=9.2, textColor=MUTED
        )


def _p(text: str, style: ParagraphStyle, unicode_font: bool) -> Paragraph:
    return Paragraph(escape(_plain(text, unicode_font)), style)


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _when(moment: datetime) -> str:
    return moment.astimezone(LAGOS).strftime("%d %b %Y")


def _stats(entries: Sequence[ExportEntry], now: datetime) -> dict[str, str]:
    year_ago = now - timedelta(days=365)
    recent = [e for e in entries if e.occurred_at >= year_ago]
    visits = [e for e in entries if e.entry_type == "visit"]
    return {
        "Crises, last 12 months": str(sum(e.entry_type == "crisis" for e in recent)),
        "Transfusions, last 12 months": str(sum(e.entry_type == "transfusion" for e in recent)),
        "Last hospital visit": _when(max(v.occurred_at for v in visits))
        if visits
        else "None recorded",
    }


def _entry_row(entry: ExportEntry, s: _Styles, uni: bool) -> list[Paragraph]:
    text = _clip(entry.summary, SUMMARY_LIMIT)
    if entry.facility_name:
        text += f"  ({_clip(entry.facility_name, FACILITY_LIMIT)})"
    return [
        _p(_when(entry.occurred_at), s.cell, uni),
        _p(TYPE_LABELS.get(entry.entry_type, entry.entry_type.title()), s.cell_b, uni),
        _p(text, s.cell, uni),
    ]


_TABLE_BASE = [
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
]


def _entries_table(rows: list[list[Paragraph]], s: _Styles, uni: bool) -> Table:
    header = [
        _p("Date", s.label, uni),
        _p("Type", s.label, uni),
        _p("What happened (where)", s.label, uni),
    ]
    table = Table([header, *rows], colWidths=ENTRY_COLS, repeatRows=0)
    table.setStyle(TableStyle([*_TABLE_BASE, ("BACKGROUND", (0, 0), (-1, 0), BAND)]))
    return table


def _height(flowable, width: float = CONTENT_W) -> float:
    _, h = flowable.wrap(width, 10_000)
    return h + flowable.getSpaceBefore() + flowable.getSpaceAfter()


# ---------------------------------------------------------------- render
def render(data: ExportInput) -> ExportResult:
    f = fonts()
    uni = f.unicode
    s = _Styles(f)
    now = data.exported_at
    stats = _stats(data.entries, now)

    head: list = [
        _p("CareKin Health Passport", s.title, uni),
        Spacer(1, 2),
        _p(
            f"Exported {now.astimezone(LAGOS).strftime('%d %b %Y, %H:%M')} WAT  |  {data.exported_for}",
            s.meta,
            uni,
        ),
        Spacer(1, 3),
        _p(DISCLAIMER, s.note, uni),
        Spacer(1, 6),
    ]

    patient = Table(
        [
            [
                _p(x, s.label, uni)
                for x in ("Name", "Date of birth (age)", "Sex", "Genotype", "Blood group")
            ],
            [
                _p(_clip(data.full_name, 80), s.cell_b, uni),
                _p(
                    f"{data.date_of_birth:%d %b %Y} ({_age(data.date_of_birth, now.date())})",
                    s.cell,
                    uni,
                ),
                _p(data.sex or "-", s.cell, uni),
                _p(data.genotype or "-", s.cell_b, uni),
                _p(data.blood_group or "-", s.cell_b, uni),
            ],
        ],
        colWidths=PATIENT_COLS,
    )
    patient.setStyle(TableStyle([*_TABLE_BASE, ("BACKGROUND", (0, 0), (-1, 0), BAND)]))
    head.append(patient)

    profile_rows = []
    if data.show_profile:
        for label, value in (
            ("Allergies", data.allergies),
            ("Known triggers", data.known_triggers),
            ("Medicines (as listed)", data.medications_overview),
            ("Care instructions", data.care_instructions),
        ):
            if value:
                profile_rows.append(
                    [_p(label, s.label, uni), _p(_clip(value, PROFILE_LIMIT), s.cell, uni)]
                )
    if profile_rows:
        head.append(_p("Care profile", s.h, uni))
        profile = Table(profile_rows, colWidths=PROFILE_COLS)
        profile.setStyle(TableStyle(_TABLE_BASE))
        head.append(profile)

    head.append(_p("At a glance", s.h, uni))
    glance = Table(
        [[_p(k, s.label, uni) for k in stats], [_p(v, s.cell_b, uni) for v in stats.values()]],
        colWidths=(CONTENT_W / 3, CONTENT_W / 3, CONTENT_W - 2 * (CONTENT_W / 3)),
    )
    glance.setStyle(TableStyle([*_TABLE_BASE, ("BACKGROUND", (0, 0), (-1, 0), BAND)]))
    head.append(glance)
    head.append(_p("Recent entries, newest first", s.h, uni))

    # Fit as many entries as the page holds.
    available = FRAME_H - 2 * FRAME_PADDING - sum(_height(x) for x in head) - 4
    rows = [_entry_row(e, s, uni) for e in data.entries]
    shown = 0
    used = _height(_entries_table([], s, uni))
    for row in rows:
        one = Table([row], colWidths=ENTRY_COLS)
        one.setStyle(TableStyle(_TABLE_BASE))
        h = _height(one)
        if used + h > available:
            break
        used += h
        shown += 1

    while True:
        story = list(head)
        if shown or not rows:
            story.append(_entries_table(rows[:shown], s, uni))
        if not rows:
            story.append(_p("No entries recorded yet.", s.note, uni))
        pdf, pages = _build(story, data, shown, f, uni)
        if pages == 1 or shown == 0:
            break
        shown -= 1  # measurement was a hair off: drop one row and rebuild
    return ExportResult(pdf, pages, shown, len(rows), uni, stats)


def _build(story: list, data: ExportInput, shown: int, f: Fonts, uni: bool) -> tuple[bytes, int]:
    total = len(data.entries)
    left_out = total - shown
    footer = f"Showing {shown} of {total} entries." + (
        f" {left_out} earlier {'entry is' if left_out == 1 else 'entries are'} in the CareKin app."
        if left_out
        else ""
    )

    def draw_footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_X, 38, MARGIN_X + CONTENT_W, 38)
        canvas.setFont(f.regular, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN_X, 27, _plain(footer, uni))
        canvas.drawRightString(MARGIN_X + CONTENT_W, 27, "CareKin Health Passport  |  Confidential")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="CareKin Health Passport",
        author="CareKin",
        subject="Patient-controlled health record",
    )
    assert abs(doc.width - CONTENT_W) < 0.01
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue(), doc.page

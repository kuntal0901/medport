"""Builds the portfolio view model and the small SVG charts."""
from collections import OrderedDict
from datetime import date
from html import escape

from .normalize import category
from .store import get_profile, rows

CATEGORY_ORDER = ["Diabetes", "Lipids", "Kidney", "Liver", "Thyroid", "Blood count", "Electrolytes",
                  "Vitamins", "Inflammation", "Other"]


# a report counts toward the portfolio unless it looks like it belongs to someone else and the user hasn't said yes
MINE = "COALESCE(d.patient_check, '') != 'mismatch'"


def _series_key(o):
    return o["canonical"] or o["test"].strip().title()


def build(conn) -> dict:
    profile = get_profile(conn)
    docs = rows(conn, f"SELECT * FROM documents d WHERE {MINE} ORDER BY report_date DESC, id DESC")
    held = rows(conn, f"SELECT * FROM documents d WHERE NOT {MINE} ORDER BY id DESC")
    obs = rows(conn, f"""SELECT o.*, d.filename, d.lab FROM observations o JOIN documents d ON d.id = o.doc_id
                        WHERE {MINE} ORDER BY o.date, o.id""")
    items = rows(conn, f"""SELECT i.*, d.filename FROM items i LEFT JOIN documents d ON d.id = i.doc_id
                          WHERE i.active = 1 AND (d.id IS NULL OR {MINE}) ORDER BY i.date DESC, i.id DESC""")

    series: dict[str, list] = {}
    for o in obs:
        series.setdefault(_series_key(o), []).append(o)

    groups: dict[str, list] = {}
    attention = []
    for name, pts in series.items():
        latest = pts[-1]
        same_unit = [p for p in pts if (p["unit"] or "").lower() == (latest["unit"] or "").lower()]
        prev = same_unit[-2] if len(same_unit) > 1 else None
        entry = {
            "name": name, "latest": latest, "prev": prev, "count": len(pts),
            "points": same_unit, "mixed_units": len(same_unit) != len(pts),
            "spark": sparkline(same_unit),
            "slug": name,
        }
        groups.setdefault(category(latest["canonical"]), []).append(entry)
        if latest["flag"] in ("high", "low"):
            attention.append(entry)
    ordered = OrderedDict()
    for cat in CATEGORY_ORDER:
        if cat in groups:
            ordered[cat] = sorted(groups[cat], key=lambda e: e["name"])

    def dedupe(kind):
        seen, out = set(), []
        for it in items:
            if it["kind"] != kind:
                continue
            key = it["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    unreviewed = sum(1 for d in docs if d["status"] != "done")
    return {
        "profile": profile, "docs": docs, "groups": ordered, "attention": attention,
        "conditions": dedupe("condition"), "medications": dedupe("medication"), "allergies": dedupe("allergy"),
        "unreviewed": unreviewed, "held": held, "today": date.today().isoformat(),
        "age": _age(profile.get("dob")),
    }


def _age(dob):
    try:
        d = date.fromisoformat(dob)
    except (TypeError, ValueError):
        return None
    t = date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _scale(points, width, height, pad):
    vals = [p["value"] for p in points if p["value"] is not None]
    lows = [p["ref_low"] for p in points if p["ref_low"] is not None]
    highs = [p["ref_high"] for p in points if p["ref_high"] is not None]
    lo = min(vals + lows[-1:]) if vals else 0
    hi = max(vals + highs[-1:]) if vals else 1
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    span = hi - lo
    lo, hi = lo - span * 0.1, hi + span * 0.1
    ords = [date.fromisoformat(p["date"]).toordinal() for p in points]
    x0, x1 = min(ords), max(ords)

    def sx(i):
        if x1 == x0:
            return width / 2
        return pad + (ords[i] - x0) / (x1 - x0) * (width - 2 * pad)

    def sy(v):
        return height - pad - (v - lo) / (hi - lo) * (height - 2 * pad)

    return sx, sy, lo, hi


def sparkline(points, width=120, height=32) -> str:
    pts = [p for p in points if p["value"] is not None and p["date"]]
    if not pts:
        return ""
    sx, sy, lo, hi = _scale(pts, width, height, 4)
    parts = [f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">']
    last = pts[-1]
    if last["ref_low"] is not None or last["ref_high"] is not None:
        top = sy(min(last["ref_high"], hi)) if last["ref_high"] is not None else 0
        bot = sy(max(last["ref_low"], lo)) if last["ref_low"] is not None else height
        parts.append(f'<rect class="band" x="0" y="{top:.1f}" width="{width}" height="{max(bot - top, 0):.1f}"/>')
    if len(pts) > 1:
        d = " ".join(f"{'M' if i == 0 else 'L'}{sx(i):.1f},{sy(p['value']):.1f}" for i, p in enumerate(pts))
        parts.append(f'<path class="line" d="{d}"/>')
    for i, p in enumerate(pts):
        cls = "dot " + (p["flag"] or "none")
        parts.append(f'<circle class="{cls}" cx="{sx(i):.1f}" cy="{sy(p["value"]):.1f}" r="{3 if i == len(pts) - 1 else 2}"/>')
    parts.append("</svg>")
    return "".join(parts)


def trend_chart(points, width=640, height=260) -> str:
    pts = [p for p in points if p["value"] is not None and p["date"]]
    if not pts:
        return "<p>No numeric values to chart.</p>"
    pad = 44
    sx, sy, lo, hi = _scale(pts, width, height, pad)
    out = [f'<svg class="trend" viewBox="0 0 {width} {height}" role="img" aria-label="Trend chart">']
    last = pts[-1]
    if last["ref_low"] is not None or last["ref_high"] is not None:
        top = sy(min(last["ref_high"], hi)) if last["ref_high"] is not None else pad
        bot = sy(max(last["ref_low"], lo)) if last["ref_low"] is not None else height - pad
        out.append(f'<rect class="band" x="{pad}" y="{top:.1f}" width="{width - 2 * pad}" height="{max(bot - top, 0):.1f}"/>')
        out.append(f'<text class="axis" x="{width - pad + 4}" y="{top + 12:.1f}">ref</text>')
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = sy(v)
        out.append(f'<line class="grid" x1="{pad}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis" x="{pad - 6}" y="{y + 4:.1f}" text-anchor="end">{v:.3g}</text>')
    if len(pts) > 1:
        d = " ".join(f"{'M' if i == 0 else 'L'}{sx(i):.1f},{sy(p['value']):.1f}" for i, p in enumerate(pts))
        out.append(f'<path class="line" pathLength="1" d="{d}"/>')  # pathLength lets CSS draw it in
    for i, p in enumerate(pts):
        x, y = sx(i), sy(p["value"])
        out.append(f'<circle class="dot {p["flag"] or "none"}" cx="{x:.1f}" cy="{y:.1f}" r="4"><title>{escape(p["date"])}: {escape(p["value_text"])} {escape(p["unit"] or "")}</title></circle>')
        out.append(f'<text class="label" x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle">{escape(p["value_text"])}</text>')
        out.append(f'<text class="axis" x="{x:.1f}" y="{height - pad + 18}" text-anchor="middle">{escape(p["date"])}</text>')
    out.append("</svg>")
    return "".join(out)

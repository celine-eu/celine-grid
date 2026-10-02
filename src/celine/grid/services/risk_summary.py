"""Pure summaries over `risk_km` tratta rows for the alert dispatcher.

Same arithmetic as the Digital Twin's `line` and `unit` grains and the table page:
km are summed, the worst level is the worst across the group, and the index is
recomputed from the sums — never averaged.
"""

from __future__ import annotations

from typing import Any

LEVEL_RANK = {"NORMAL": 0, "WARNING": 1, "ALERT": 2}

# A threshold is a floor: WARNING admits WARNING and ALERT, ALERT only ALERT.
# Unknown thresholds fall back to the strict floor (under-alert, never over-alert).
_FLOORS = {"WARNING", "ALERT"}


def _num(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _floor(threshold: str | None) -> str:
    return threshold if threshold in _FLOORS else "ALERT"


def km_at_or_above(row: dict[str, Any], threshold: str | None) -> float:
    """Kilometres of the row at or above the threshold floor."""
    km = _num(row.get("km_alert"))
    if _floor(threshold) == "WARNING":
        km += _num(row.get("km_warning"))
    return km


def triggered_vectors(
    rows: list[dict[str, Any]], threshold: str | None, risk_types: list[str]
) -> list[str]:
    """The hazards among `risk_types` that have any km at or above the threshold."""
    out: list[str] = []
    for vector in risk_types:
        if any(
            row.get("risk_vector") == vector and km_at_or_above(row, threshold) > 0
            for row in rows
        ):
            out.append(vector)
    return out


def _pct(part: float, total: float) -> float | None:
    return round(100.0 * part / total, 1) if total else None


def summarise(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Sum km per distinct value of `key`, sorted by index (desc) then key."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or ""), []).append(row)

    out: list[dict[str, Any]] = []
    for k, group in groups.items():
        km_total = sum(_num(r.get("km_total")) for r in group)
        km_alert = sum(_num(r.get("km_alert")) for r in group)
        km_warning = sum(_num(r.get("km_warning")) for r in group)
        worst = max((str(r.get("worst_level") or "NORMAL") for r in group), key=lambda lvl: LEVEL_RANK.get(lvl, 0))
        index = round(100.0 * (km_alert + 0.5 * km_warning) / km_total, 1) if km_total else None
        lines = summarise_lines_within(group) if key != "line_name" else []
        out.append(
            {
                "key": k,
                "n_tratte": len(group),
                "km_total": round(km_total, 2),
                "km_alert": round(km_alert, 2),
                "km_warning": round(km_warning, 2),
                "pct_alert": _pct(km_alert, km_total),
                "pct_warning": _pct(km_warning, km_total),
                "risk_index": index,
                "worst_level": worst,
                "operational_units": sorted({str(r["operational_unit"]) for r in group if r.get("operational_unit")}),
                "worst_lines": [line["key"] for line in lines[:3] if km_at_or_above(line, "WARNING") > 0],
            }
        )
    out.sort(key=lambda u: (-(u["risk_index"] if u["risk_index"] is not None else -1), u["key"]))
    return out


def summarise_lines_within(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-line summary used for the worst lines of a unit (no recursion)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("line_name") or ""), []).append(row)
    out: list[dict[str, Any]] = []
    for k, group in groups.items():
        km_total = sum(_num(r.get("km_total")) for r in group)
        km_alert = sum(_num(r.get("km_alert")) for r in group)
        km_warning = sum(_num(r.get("km_warning")) for r in group)
        index = round(100.0 * (km_alert + 0.5 * km_warning) / km_total, 1) if km_total else None
        out.append({"key": k, "km_total": km_total, "km_alert": km_alert, "km_warning": km_warning, "risk_index": index})
    out.sort(key=lambda u: (-(u["risk_index"] if u["risk_index"] is not None else -1), -u["km_alert"], u["key"]))
    return out


def build_days(
    rows: list[dict[str, Any]],
    dates: list[str],
    vectors: list[str],
    *,
    threshold: str | None = "ALERT",
    top_n: int = 15,
) -> list[dict[str, Any]]:
    """The report body: for each date and hazard, units by index, the worst lines, totals."""
    days: list[dict[str, Any]] = []
    for date in dates:
        day_vectors: list[dict[str, Any]] = []
        for vector in vectors:
            subset = [r for r in rows if r.get("date") == date and r.get("risk_vector") == vector]
            units = [
                {
                    "operational_unit": u["key"],
                    "n_tratte": u["n_tratte"],
                    "km_total": u["km_total"],
                    "km_alert": u["km_alert"],
                    "km_warning": u["km_warning"],
                    "pct_alert": u["pct_alert"],
                    "pct_warning": u["pct_warning"],
                    "risk_index": u["risk_index"],
                    "worst_level": u["worst_level"],
                    "worst_lines": u["worst_lines"],
                }
                for u in summarise(subset, "operational_unit")
            ]
            lines = [
                {
                    "line_name": line["key"],
                    "operational_units": line["operational_units"],
                    "km_total": line["km_total"],
                    "km_alert": line["km_alert"],
                    "km_warning": line["km_warning"],
                    "pct_alert": line["pct_alert"],
                    "risk_index": line["risk_index"],
                    "worst_level": line["worst_level"],
                }
                for line in summarise(subset, "line_name")
                if km_at_or_above(line, threshold) > 0
            ][:top_n]
            totals_list = summarise([{**r, "_all": "*"} for r in subset], "_all")
            totals = (
                {k: v for k, v in totals_list[0].items() if k not in ("key", "operational_units", "worst_lines")}
                if totals_list
                else {"n_tratte": 0, "km_total": 0.0, "km_alert": 0.0, "km_warning": 0.0, "pct_alert": None, "pct_warning": None, "risk_index": None, "worst_level": "NORMAL"}
            )
            day_vectors.append({"risk_vector": vector, "units": units, "top_lines": lines, "totals": totals})
        days.append({"date": date, "vectors": day_vectors})
    return days

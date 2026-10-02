"""Grid data proxy endpoints — forward to DT via SDK GridClient."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from celine.grid.api.deps import DTDep, NetworkReadDep
from celine.sdk.dt.util import DTApiError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/grid/{network_id}", tags=["grid"])

# Common filter query params used across most endpoints
_DATES = Query(None)
_UNIT = Query(None)
_LINE = Query(None)
_SUB = Query(None)


def _dt_error(exc: DTApiError, label: str) -> HTTPException:
    log.error("%s failed (status=%s): %s", label, exc.status_code, exc)
    code = exc.status_code or 502
    return HTTPException(code, f"DT error: {label}")


def _to_feature_collection(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows carrying a `feature_geojson` column → GeoJSON FeatureCollection.

    The geometry is taken from the column (a JSON string or an object, wrapped in a
    Feature or bare); the raw `geom` column is dropped; every other column becomes a
    feature property. A row with no usable geometry is skipped, not fatal.
    """
    import json as _json

    features = []
    for item in items:
        raw = item.pop("feature_geojson", None)
        item.pop("geom", None)

        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
            except Exception:
                continue
        else:
            parsed = raw

        geometry = parsed.get("geometry", parsed) if isinstance(parsed, dict) else None
        if not geometry:
            continue

        features.append({"type": "Feature", "geometry": geometry, "properties": item})

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------

@router.get("/wind/map")
async def wind_map(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
    risk_level: list[str] | None = Query(None),
) -> dict[str, Any]:
    try:
        return await dt.grid.wind_map(
            network_id,
            dates=dates,
            operational_unit=operational_unit,
            line_name=line_name,
            substation_name=substation_name,
            risk_level=risk_level,
        )
    except DTApiError as e:
        raise _dt_error(e, "wind_map")


@router.get("/wind/bosco")
async def wind_bosco(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
) -> dict[str, Any]:
    try:
        return await dt.grid.wind_bosco(
            network_id,
            dates=dates,
            operational_unit=operational_unit,
            line_name=line_name,
            substation_name=substation_name,
        )
    except DTApiError as e:
        raise _dt_error(e, "wind_bosco")


@router.get("/wind/alert-distribution")
async def wind_alert_distribution(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
) -> list[dict[str, Any]]:
    try:
        return await dt.grid.wind_alert_distribution(
            network_id,
            dates=dates,
            operational_unit=operational_unit,
            line_name=line_name,
            substation_name=substation_name,
        )
    except DTApiError as e:
        raise _dt_error(e, "wind_alert_distribution")


@router.get("/wind/trend")
async def wind_trend(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
) -> list[dict[str, Any]]:
    try:
        return await dt.grid.wind_trend(network_id)
    except DTApiError as e:
        raise _dt_error(e, "wind_trend")


# ---------------------------------------------------------------------------
# Heat
# ---------------------------------------------------------------------------

@router.get("/heat/map")
async def heat_map(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
    risk_level: list[str] | None = Query(None),
) -> dict[str, Any]:
    try:
        return await dt.grid.heat_map(
            network_id,
            dates=dates,
            operational_unit=operational_unit,
            line_name=line_name,
            substation_name=substation_name,
            risk_level=risk_level,
        )
    except DTApiError as e:
        raise _dt_error(e, "heat_map")


@router.get("/heat/alert-distribution")
async def heat_alert_distribution(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
) -> list[dict[str, Any]]:
    try:
        return await dt.grid.heat_alert_distribution(
            network_id,
            dates=dates,
            operational_unit=operational_unit,
            line_name=line_name,
            substation_name=substation_name,
        )
    except DTApiError as e:
        raise _dt_error(e, "heat_alert_distribution")


@router.get("/heat/trend")
async def heat_trend(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
) -> list[dict[str, Any]]:
    try:
        return await dt.grid.heat_trend(network_id)
    except DTApiError as e:
        raise _dt_error(e, "heat_trend")


# ---------------------------------------------------------------------------
# Substations
# ---------------------------------------------------------------------------

@router.get("/substations/map")
async def substations_map(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
) -> dict[str, Any]:
    try:
        return await dt.grid.substations_map(network_id)
    except DTApiError as e:
        raise _dt_error(e, "substations_map")


# ---------------------------------------------------------------------------
# Filter metadata
# ---------------------------------------------------------------------------

@router.get("/filters")
async def get_filters(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
) -> dict[str, Any]:
    try:
        return await dt.grid.filters(network_id)
    except DTApiError as e:
        raise _dt_error(e, "filters")


# ---------------------------------------------------------------------------
# Shapes / Risks / Trendline  (ValueFetcherSpec-backed endpoints)
# ---------------------------------------------------------------------------

@router.get("/tile-index")
async def tile_index(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    response: Response,
) -> dict[str, Any]:
    """Tile catalog for progressive shape loading — one row per 5 km tile."""
    try:
        result = await dt.grid.tile_index(network_id)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "tile_index")


@router.get("/shapes")
async def shapes(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    response: Response,
    asset_type: list[str] | None = Query(None),
    tile_id: list[str] | None = Query(None),
) -> dict[str, Any]:
    """Static CIM asset topology as GeoJSON FeatureCollection.

    Pass tile_id for progressive loading (e.g. ?tile_id=tile_0_3&tile_id=tile_1_3).
    Omit tile_id to load all shapes (backward compatible).
    """
    try:
        result = await dt.grid.shapes(network_id, asset_type=asset_type, tile_ids=tile_id)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return _to_feature_collection(result.to_dict()["items"])
    except DTApiError as e:
        raise _dt_error(e, "shapes")


_TILE_IDS = Query(None)


@router.get("/tree-strike-spans")
async def tree_strike_spans(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    response: Response,
    tile_id: list[str] | None = _TILE_IDS,
) -> dict[str, Any]:
    """Tree-strike exposure spans (static overlay) as a GeoJSON FeatureCollection.

    Same tile ids as /shapes; omit tile_id for the whole overlay. Goes through the
    SDK's generic values call (no SDK release needed).
    """
    payload: dict[str, Any] = {}
    if tile_id:
        payload["tile_ids"] = tile_id
    try:
        result = await dt.grid.fetch_values(network_id, "tree_strike_spans", payload, limit=5000)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return _to_feature_collection(result.to_dict()["items"])
    except DTApiError as e:
        raise _dt_error(e, "tree_strike_spans")


@router.get("/risks")
async def risks(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] | None = _DATES,
    risk_vector: list[str] | None = Query(None),
) -> dict[str, Any]:
    """WARNING/ALERT risk rows for given dates — no geometry."""
    try:
        result = await dt.grid.risks(
            network_id,
            dates=dates or [],
            risk_vector=risk_vector,
        )
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "risks")


@router.get("/risks-now")
async def risks_now(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    risk_vector: list[str] | None = Query(None),
) -> dict[str, Any]:
    """Nowcasting risk rows — current observations, no date filter."""
    try:
        result = await dt.grid.risks_now(
            network_id,
            risk_vector=risk_vector,
        )
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "risks_now")


_RISK_KM_DATES = Query(..., min_length=1)
_RISK_KM_LEVEL = Query(None, pattern="^(tratta|line|unit)$")
_RISK_KM_MIN_LEVEL = Query(None, pattern="^(WARNING|ALERT)$")
_RISK_VECTOR = Query(None)


@router.get("/risk-km")
async def risk_km(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] = _RISK_KM_DATES,
    level: str | None = _RISK_KM_LEVEL,
    risk_vector: list[str] | None = _RISK_VECTOR,
    operational_unit: list[str] | None = _UNIT,
    line_name: list[str] | None = _LINE,
    substation_name: list[str] | None = _SUB,
    min_level: str | None = _RISK_KM_MIN_LEVEL,
) -> dict[str, Any]:
    """Length-weighted risk exposure (km at ALERT/WARNING, 0-100 index).

    `level` picks the grain: tratta (default, as on the map), line, or operational
    unit. Goes through the SDK's generic values call so the service does not need
    an SDK release to reach the `risk_km` fetcher.
    """
    payload: dict[str, Any] = {"dates": dates}
    if level:
        payload["level"] = level
    if risk_vector:
        payload["risk_vector"] = risk_vector
    if operational_unit:
        payload["operational_unit"] = operational_unit
    if line_name:
        payload["line_name"] = line_name
    if substation_name:
        payload["substation_name"] = substation_name
    if min_level:
        payload["min_level"] = min_level
    try:
        result = await dt.grid.fetch_values(network_id, "risk_km", payload, limit=20000)
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "risk_km")


_SLOTS = Query(None)


@router.get("/risks-8h")
async def risks_8h(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    dates: list[str] = _RISK_KM_DATES,
    slot: list[int] | None = _SLOTS,
    risk_vector: list[str] | None = _RISK_VECTOR,
) -> dict[str, Any]:
    """WARNING/ALERT risk rows per 8-hour window (intra-day view) — no geometry.

    Same shape as /risks plus window_start and slot (0 = 00–08, 1 = 08–16,
    2 = 16–24). Goes through the SDK's generic values call.
    """
    if slot and any(value < 0 or value > 2 for value in slot):
        raise HTTPException(422, "slot must be 0, 1 or 2")
    payload: dict[str, Any] = {"dates": dates}
    if slot:
        payload["slots"] = slot
    if risk_vector:
        payload["risk_vector"] = risk_vector
    try:
        result = await dt.grid.fetch_values(network_id, "risks_8h", payload, limit=30000)
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "risks_8h")


@router.get("/trendline")
async def trendline(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
    date_from: str = Query(...),
    date_to: str = Query(...),
    risk_vector: list[str] | None = Query(None),
) -> dict[str, Any]:
    """Daily risk percentage indicator per vector."""
    try:
        result = await dt.grid.trendline(
            network_id,
            date_from=date_from,
            date_to=date_to,
            risk_vector=risk_vector,
        )
        return result.to_dict()
    except DTApiError as e:
        raise _dt_error(e, "trendline")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@router.get("/summary")
async def summary(
    network_id: str,
    _user: NetworkReadDep,
    dt: DTDep,
) -> dict[str, Any]:
    try:
        return await dt.grid.summary(network_id)
    except DTApiError as e:
        raise _dt_error(e, "summary")

"""Grid data proxy endpoints — forward to DT via SDK GridClient."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

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
) -> dict[str, list[str]]:
    try:
        return await dt.grid.filters(network_id)
    except DTApiError as e:
        raise _dt_error(e, "filters")


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

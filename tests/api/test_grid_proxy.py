"""The Digital Twin proxy: who may ask, what is forwarded, what comes back.

The `network_id` is a path segment, so every one of these routes would happily serve
another DSO's grid if the ownership check were removed — and the Digital Twin would not
object, because it answers for whatever network it is asked about. There is no shape
difference between the right network's data and the wrong one's. That is what the first
half of this file is about.

The second half is the one piece of logic in `api/grid.py` that is not a pass-through:
the GeoJSON assembly in `/shapes`.
"""

from __future__ import annotations

import pytest
from celine.sdk.dt.util import DTApiError

from tests.conftest import NETWORK
from tests.fakes import FetchResult

# Every route under /api/grid/{network_id}, and the DT call each one makes.
ROUTES = [
    ("/wind/map", "wind_map"),
    ("/wind/bosco", "wind_bosco"),
    ("/wind/alert-distribution", "wind_alert_distribution"),
    ("/wind/trend", "wind_trend"),
    ("/heat/map", "heat_map"),
    ("/heat/alert-distribution", "heat_alert_distribution"),
    ("/heat/trend", "heat_trend"),
    ("/substations/map", "substations_map"),
    ("/filters", "filters"),
    ("/summary", "summary"),
    ("/tile-index", "tile_index"),
    ("/shapes", "shapes"),
    ("/risks", "risks"),
    ("/risks-now", "risks_now"),
    ("/trendline?date_from=2026-08-01&date_to=2026-08-15", "trendline"),
]

ROUTE_IDS = [call for _path, call in ROUTES]


def url(path: str, network: str = NETWORK) -> str:
    return f"/api/grid/{network}{path}"


# ---------------------------------------------------------------------------
# Network ownership, on every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "call"), ROUTES, ids=ROUTE_IDS)
# @verifies REQ-0023
async def test_an_operator_may_read_their_own_network(client, dso_user, dt, path, call):
    response = await client.get(url(path), headers=dso_user)

    assert response.status_code == 200, response.text
    assert dt.grid.called(call) == 1


@pytest.mark.parametrize(("path", "call"), ROUTES, ids=ROUTE_IDS)
# @verifies REQ-0023
async def test_no_route_serves_another_dso_s_network(
    client, other_dso_user, dt, path, call
):
    """
    Parametrised over every route on purpose. The check is a dependency that must be
    declared per endpoint (`_user: NetworkReadDep`), so a new route added without it
    would be open — and would look exactly like the others in review. This test is what
    notices.
    """
    response = await client.get(url(path), headers=other_dso_user)

    assert response.status_code == 403
    assert dt.grid.calls == [], "the Digital Twin was queried despite the refusal"


@pytest.mark.parametrize(("path", "call"), ROUTES, ids=ROUTE_IDS)
# @verifies REQ-0001
async def test_no_route_answers_without_a_token(client, dt, path, call):
    assert (await client.get(url(path))).status_code == 401
    assert dt.grid.calls == []


# @verifies REQ-0005
async def test_a_caller_with_no_organisation_is_refused(client, orgless_user, dt):
    assert (await client.get(url("/summary"), headers=orgless_user)).status_code == 403


# @verifies REQ-0023
async def test_the_network_id_from_the_path_is_what_is_forwarded(client, dso_user, dt):
    """
    Not the token's network — the path's, after the two have been checked equal. Worth
    pinning: if the forwarded value were ever taken from the token instead, the
    ownership check would become decorative and could be removed without any test
    noticing.
    """
    await client.get(url("/summary"), headers=dso_user)

    called, args, _kwargs = dt.grid.calls[0]
    assert args == (NETWORK,)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


# @verifies REQ-0024
async def test_repeated_filter_parameters_are_forwarded_as_lists(client, dso_user, dt):
    """
    The frontend sends `?dates=a&dates=b`, and the DT expects a list. A framework that
    took only the last value would silently narrow every filtered view to one day.
    """
    await client.get(
        url("/wind/map")
        + "?dates=2026-08-14&dates=2026-08-15"
        + "&operational_unit=north&line_name=L1&substation_name=S1&risk_level=ALERT",
        headers=dso_user,
    )

    kwargs = dt.grid.call_kwargs("wind_map")
    assert kwargs["dates"] == ["2026-08-14", "2026-08-15"]
    assert kwargs["operational_unit"] == ["north"]
    assert kwargs["risk_level"] == ["ALERT"]


# @verifies REQ-0024
async def test_an_absent_filter_is_forwarded_as_none_not_an_empty_list(
    client, dso_user, dt
):
    """
    The SDK maps `None` to `UNSET` and omits the parameter; an empty list would be sent
    as `?dates=` and could be read upstream as "match nothing".
    """
    await client.get(url("/wind/map"), headers=dso_user)

    assert dt.grid.call_kwargs("wind_map")["dates"] is None


# @verifies REQ-0024
async def test_risks_sends_an_empty_list_rather_than_none_for_dates(
    client, dso_user, dt
):
    """
    `/risks` alone coerces `dates or []`. Pinned because it differs from every other
    route here and the difference is one `or` easily lost in an edit.
    """
    await client.get(url("/risks"), headers=dso_user)

    assert dt.grid.call_kwargs("risks")["dates"] == []


# @verifies REQ-0024
async def test_trendline_requires_its_date_range(client, dso_user, dt):
    """
    The only required query parameters in the whole proxy.
    """
    assert (await client.get(url("/trendline"), headers=dso_user)).status_code == 422
    assert dt.grid.calls == []


# ---------------------------------------------------------------------------
# What an upstream failure becomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 422, 500, 503])
# @verifies REQ-0025
async def test_a_digital_twin_error_keeps_its_own_status(client, dso_user, dt, status):
    """
    Including 500: a DT fault is reported to the frontend as a 500 from *this* service,
    which makes the two indistinguishable in the browser. The log line names the
    endpoint; the response deliberately does not, carrying only `DT error: <label>`.
    """
    dt.grid.set("summary", DTApiError("upstream said no", status_code=status))

    response = await client.get(url("/summary"), headers=dso_user)

    assert response.status_code == status
    assert response.json()["detail"] == "DT error: summary"


# @verifies REQ-0025
async def test_a_digital_twin_error_with_no_status_becomes_a_502(client, dso_user, dt):
    """
    `status_code=None` is what `DTApiError` carries when the request never got a
    response at all — a connection refused, a timeout. 502 is the honest answer.
    """
    dt.grid.set("summary", DTApiError("connection refused"))

    assert (await client.get(url("/summary"), headers=dso_user)).status_code == 502


# @verifies REQ-0025
async def test_an_error_that_is_not_a_dt_error_is_not_caught(client, dso_user, dt):
    """
    Only `DTApiError` is handled. Anything else — a TypeError from a changed SDK
    response shape, say — propagates as an unhandled 500. That is the shape an SDK bump
    takes here, and it is why a green suite says nothing about an SDK upgrade.
    """
    dt.grid.set("summary", TypeError("SDK returned something new"))

    with pytest.raises(TypeError):
        await client.get(url("/summary"), headers=dso_user)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/tile-index", "/shapes"])
# @verifies REQ-0028
async def test_the_static_topology_is_cacheable_for_an_hour(client, dso_user, path):
    """
    CIM topology changes when the grid is rebuilt, not when the weather does. The
    header is `public`, and the response is scoped to one network by its path — which
    is what makes a shared cache safe here and would stop being true if the network
    ever moved into a header or a token.
    """
    response = await client.get(url(path), headers=dso_user)

    assert response.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.parametrize("path", ["/risks", "/risks-now", "/summary", "/wind/map"])
# @verifies REQ-0028
async def test_the_risk_surfaces_are_not_cached(client, dso_user, path):
    """
    These change with every pipeline run; a cached one is a stale alert.
    """
    response = await client.get(url(path), headers=dso_user)

    assert "cache-control" not in response.headers


# ---------------------------------------------------------------------------
# /shapes — the one transformation
# ---------------------------------------------------------------------------


# @verifies REQ-0026
async def test_shapes_are_assembled_into_a_feature_collection(client, dso_user, dt):
    """
    The DT returns rows; the map library wants GeoJSON. Everything on the row other
    than the geometry becomes the feature's properties.
    """
    dt.grid.set(
        "shapes",
        FetchResult(
            [
                {
                    "asset_id": "L1",
                    "asset_type": "line",
                    "feature_geojson": '{"type":"Feature","geometry":{"type":"LineString","coordinates":[[0,0],[1,1]]}}',
                }
            ]
        ),
    )

    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 1
    feature = body["features"][0]
    assert feature["geometry"] == {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    assert feature["properties"] == {"asset_id": "L1", "asset_type": "line"}


# @verifies REQ-0026
async def test_a_geometry_arriving_as_an_object_is_accepted_too(client, dso_user, dt):
    """
    `feature_geojson` is a string when the DT hands back a raw column and a dict when
    it has parsed it. Both are in production.
    """
    dt.grid.set(
        "shapes",
        FetchResult(
            [{"asset_id": "S1", "feature_geojson": {"geometry": {"type": "Point", "coordinates": [1, 2]}}}]
        ),
    )

    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert body["features"][0]["geometry"] == {"type": "Point", "coordinates": [1, 2]}


# @verifies REQ-0026
async def test_a_bare_geometry_without_a_feature_wrapper_is_accepted(
    client, dso_user, dt
):
    """
    `parsed.get("geometry", parsed)` — a row carrying the geometry directly, with no
    Feature around it, is used as-is.
    """
    dt.grid.set(
        "shapes",
        FetchResult([{"asset_id": "S1", "feature_geojson": {"type": "Point", "coordinates": [1, 2]}}]),
    )

    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert body["features"][0]["geometry"]["type"] == "Point"


# @verifies REQ-0026
async def test_the_raw_geometry_column_never_reaches_the_frontend(client, dso_user, dt):
    """
    `geom` is the database's own binary/WKB column. It is popped rather than sent: it
    is large, it is unusable in the browser, and it would be repeated on every feature.
    """
    dt.grid.set(
        "shapes",
        FetchResult(
            [
                {
                    "asset_id": "L1",
                    "geom": "0102000020E6100000",
                    "feature_geojson": {"type": "Point", "coordinates": [0, 0]},
                }
            ]
        ),
    )

    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert "geom" not in body["features"][0]["properties"]


@pytest.mark.parametrize(
    "row",
    [
        {"asset_id": "X"},
        {"asset_id": "X", "feature_geojson": None},
        {"asset_id": "X", "feature_geojson": "not json at all"},
        {"asset_id": "X", "feature_geojson": '{"geometry": null}'},
        {"asset_id": "X", "feature_geojson": 42},
    ],
    ids=["absent", "null", "unparseable", "null-geometry", "not-a-mapping"],
)
# @verifies REQ-0027
async def test_a_row_with_no_usable_geometry_is_dropped_not_fatal(
    client, dso_user, dt, row
):
    """
    One bad row out of ten thousand must not empty the map. It is dropped **silently** —
    no count, no log line — so a systematic geometry failure upstream shows up as a map
    that is simply missing assets, with nothing anywhere saying how many. See
    `.agents/knowledge/silence-is-the-failure-mode.md`.
    """
    dt.grid.set(
        "shapes",
        FetchResult([row, {"asset_id": "good", "feature_geojson": {"type": "Point", "coordinates": [0, 0]}}]),
    )

    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert [f["properties"]["asset_id"] for f in body["features"]] == ["good"]


# @verifies REQ-0026
async def test_shapes_can_be_requested_a_tile_at_a_time(client, dso_user, dt):
    """
    Progressive loading: the frontend reads `/tile-index` first, then asks for the
    tiles in view. Omitting `tile_id` loads everything, which is the older behaviour
    and still supported.
    """
    await client.get(
        url("/shapes") + "?tile_id=tile_0_3&tile_id=tile_1_3&asset_type=line",
        headers=dso_user,
    )

    kwargs = dt.grid.call_kwargs("shapes")
    assert kwargs["tile_ids"] == ["tile_0_3", "tile_1_3"]
    assert kwargs["asset_type"] == ["line"]


# @verifies REQ-0027
async def test_no_shapes_is_an_empty_feature_collection(client, dso_user, dt):
    """
    Not a 404. An empty collection is what the map library renders as "nothing here".
    """
    body = (await client.get(url("/shapes"), headers=dso_user)).json()

    assert body == {"type": "FeatureCollection", "features": []}

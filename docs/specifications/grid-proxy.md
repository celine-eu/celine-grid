# The grid data proxy

Eighteen routes under `/api/grid/{network_id}/`, all of which forward a request to the
Digital Twin and return what comes back. Two of them, `/shapes` and `/tree-strike-spans`,
assemble the rows into GeoJSON — the only transformation this service applies to grid data.

---

### REQ-0022 — the grid routes fail loudly when the Digital Twin is unconfigured

`503`, with `Digital Twin API not configured`, when `DIGITAL_TWIN_API_URL` is unset.

This is the one dependency in the service that refuses rather than degrading, and it
should stay that way: a silent empty map reads as "no risk today".

### REQ-0023 — every route checks that the caller owns the network in its path

The `network_id` is a path segment, so each route would otherwise serve any DSO's grid to
any operator. The check is a per-endpoint dependency (`NetworkReadDep`), which means a new
route added without it is open and looks in review exactly like the others.

The Digital Twin is not queried when the check refuses, and the value forwarded upstream
is the one from the path — after it has been established equal to the caller's.

### REQ-0024 — filters are forwarded as given

Repeated query parameters (`?dates=a&dates=b`) arrive at the Digital Twin as lists. An
absent filter is forwarded as `None`, which the SDK omits from the request — not as an
empty list, which could be read upstream as "match nothing".

`/risks` is the exception: it coerces absent dates to `[]`. `/trendline` is the only route
with required parameters, `date_from` and `date_to`.

### REQ-0025 — a Digital Twin error keeps its own status, or becomes a 502

A `DTApiError` carrying a status is answered with that status; one carrying none — a
refused connection, a timeout — is answered `502`.

The response body names only the operation (`DT error: summary`), never the upstream URL
or message. Two consequences worth stating: an upstream `500` is indistinguishable in the
browser from a fault in this service, and only `DTApiError` is caught — anything else,
such as a `TypeError` from a changed SDK response shape, propagates as an unhandled `500`.

### REQ-0026 — `/shapes` returns a GeoJSON FeatureCollection

The Digital Twin returns rows; the map library wants GeoJSON. Each row's
`feature_geojson` becomes a feature's geometry and everything else on the row becomes its
properties.

`feature_geojson` is accepted as a JSON string or as an object, wrapped in a Feature or as
a bare geometry — all four are in production. The raw `geom` column is dropped rather than
forwarded: it is large, unusable in a browser, and would repeat on every feature.

Shapes may be requested a tile at a time (`?tile_id=…`, from `/tile-index`) or all at
once.

### REQ-0027 — a row with no usable geometry is dropped, not fatal

Absent, `null`, unparseable, or carrying a null geometry: the row is skipped and the rest
of the collection is returned. One bad row out of ten thousand must not empty the map.

No geometry at all is an empty FeatureCollection, not a `404`.

The drop is **silent** — no count, no log line — so a systematic geometry failure upstream
appears as a map quietly missing assets.

### REQ-0028 — the static topology is cacheable for an hour; the risk surfaces are not

`/tile-index` and `/shapes` return `Cache-Control: public, max-age=3600`. CIM topology
changes when the grid is rebuilt, not when the weather does.

Everything else — the maps, distributions, trends, risks and summary — sets no cache
header, because each changes with every pipeline run and a cached one is a stale alert.

`public` is safe only because the network is a path segment, so two networks never share a
cache entry. Moving the network into a header or deriving it from the token would break
that.

### REQ-0045 — `/risk-km` forwards to the Digital Twin's `risk_km` fetcher through the generic values call

`GET /risk-km?dates=…` returns the length-weighted risk exposure rows (km at ALERT and
WARNING, static tree-strike km, a 0–100 index) at the grain named by `level` — `tratta`
(the map's line × municipality × conductor rows, split by operational unit), `line` or
`unit`. `dates` is required (at least one); `level`, `risk_vector`, `operational_unit`,
`line_name`, `substation_name` and `min_level` are forwarded only when given, so the
Digital Twin's own defaults apply. `level` outside `tratta | line | unit` and `min_level`
outside `WARNING | ALERT` are refused with `422` before anything is forwarded.

The call is `fetch_values(network_id, "risk_km", payload, limit=20000)` — the SDK's
generic values entry point — rather than a named client method, so the service reaches the
fetcher on the SDK version it is already pinned to. The result is returned as the fetch
result's dictionary (`items`, `count`, pagination), not re-shaped.

### REQ-0047 — `/tree-strike-spans` returns the exposure overlay as a GeoJSON FeatureCollection, assembled like `/shapes`

`GET /tree-strike-spans[?tile_id=…]` forwards to the Digital Twin's `tree_strike_spans`
fetcher through the generic values call (`limit` 5000, the whole overlay) — `tile_id`
values, the same ids as `/shapes`, are forwarded as `tile_ids` only when given — and
assembles the rows exactly as REQ-0026/REQ-0027 describe for `/shapes`: the geometry from
`feature_geojson`, the raw `geom` dropped, every other column a property, a row with no
usable geometry skipped. Being static topology it is cacheable for an hour (REQ-0028).

### REQ-0048 — `/risks-8h` forwards to the Digital Twin's `risks_8h` fetcher through the generic values call

`GET /risks-8h?dates=…[&slot=…][&risk_vector=…]` returns the WARNING/ALERT rows per
segment and 8-hour window (`window_start`, `slot` 0 = 00–08, 1 = 08–16, 2 = 16–24) —
the intra-day companion of `/risks`, same row layout otherwise. `dates` is required (at
least one); `slot` values outside 0..2 are refused with `422` before anything is
forwarded; `slot` and `risk_vector` are forwarded only when given. The call is
`fetch_values(network_id, "risks_8h", payload, limit=30000)`; the fetch result is
returned as its dictionary, not re-shaped, and it is not cached (REQ-0028).

### REQ-0049 — `/shapes` forwards every `asset_type` value verbatim, `joint` included

`asset_type` is an opaque list of strings passed through to the Digital Twin's `shapes`
fetcher unmodified: the proxy applies no allow-list and no rewriting, so `?asset_type=joint`
reaches the Digital Twin exactly as `?asset_type=line` or `?asset_type=cable` would. A
joint row is assembled into a GeoJSON feature the same way every other shape row is
(REQ-0026/REQ-0027): the geometry comes from `feature_geojson` (here a `Point` rather
than a `LineString`), and every other column, thermal ones included
(`thermal_tier`, `thermal_margin_c`, `thermal_theta_max_c`, `thermal_insulation`,
`is_asphalt`, `anno_posa`, `technology`, `m_r_critico`), becomes a feature property
without the endpoint needing to know what any of them mean.

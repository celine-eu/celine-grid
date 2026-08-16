# The grid data proxy

Fifteen routes under `/api/grid/{network_id}/`, fourteen of which forward a request to the
Digital Twin and return what comes back. The fifteenth, `/shapes`, is the only place this
service transforms grid data at all.

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

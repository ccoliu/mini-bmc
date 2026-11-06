"""Query parameters (DSP0266 "Query parameters"): which ones the service accepts, and where.

- An unknown parameter that does not start with $ is ignored, as the spec requires.
- Any other $ parameter is 501 QueryParameterUnsupported: silently ignoring `$top` or
  `$filter` would answer as if the client's filter had been applied.
- `$expand` works only on the routes in EXPANDABLE; elsewhere it is 400
  QueryNotSupportedOnResource, so a client is never left thinking it got members expanded.

Accepted `$expand` forms: `*` or `.` (the same here, since no expandable resource has a
Links section), optionally with `($levels=1)`. `~` is valid Redfish that we do not
implement (501); anything else is malformed (400).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from .errors import RedfishError
from .odata import ROOT

EXPAND = "$expand"
MAX_LEVELS = 1

# Route paths, as declared (with their {placeholders}), that accept $expand.
EXPANDABLE = frozenset({f"{ROOT}/Chassis/{{chassis_id}}/Sensors"})

# ServiceRoot ProtocolFeaturesSupported: exactly what this module implements.
PROTOCOL_FEATURES: dict[str, Any] = {
    "ExpandQuery": {
        "ExpandAll": True,  # *
        "NoLinks": True,  # .
        "Links": False,  # ~
        "Levels": True,
        "MaxLevels": MAX_LEVELS,
    },
    "ExcerptQuery": False,
    "FilterQuery": False,
    "OnlyMemberQuery": False,
    "SelectQuery": False,
    "TopSkipQuery": False,
}

EXPAND_FORM = re.compile(r"(?P<kind>[*.~])(?:\(\$levels=(?P<levels>\d+)\))?")


async def check_query(request: Request) -> None:
    """App-wide dependency: rejects $ parameters before any route runs."""
    params = request.query_params
    for name in params:
        if name.startswith("$") and name != EXPAND:
            raise RedfishError(501, "QueryParameterUnsupported", name)
    if EXPAND in params:
        route = request.scope.get("route")
        if getattr(route, "path", None) not in EXPANDABLE:
            raise RedfishError(400, "QueryNotSupportedOnResource")


async def expand_levels(request: Request) -> int:
    """Route dependency: how many levels to expand, 0 without $expand. Only an EXPANDABLE
    route uses it; check_query has already turned $expand away everywhere else."""
    values = request.query_params.getlist(EXPAND)
    if not values:
        return 0
    value = ",".join(values)  # a repeated $expand is malformed too
    match = EXPAND_FORM.fullmatch(value)
    if match is None:
        raise RedfishError(400, "QueryParameterValueFormatError", value, EXPAND)
    if match["kind"] == "~":
        raise RedfishError(501, "QueryParameterUnsupported", f"{EXPAND}={value}")
    levels = int(match["levels"] or 1)
    if not 1 <= levels <= MAX_LEVELS:
        raise RedfishError(
            400, "QueryParameterOutOfRange", match["levels"], "$levels", f"1 to {MAX_LEVELS}"
        )
    return levels

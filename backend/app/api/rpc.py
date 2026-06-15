"""Helper for endpoints that forward a PostgreSQL RPC function's jsonb result.

The dashboard / sites / access / outage pages get their data from SQL functions
returning ``jsonb`` (``SELECT fn_*()``). For an ad-hoc text() select, the asyncpg
driver returns ``jsonb`` as the raw JSON **string** (it only decodes columns the
ORM has typed as JSON/JSONB). So we must parse it before handing it to FastAPI,
which would otherwise double-encode the string.

``scalar_json`` is defensive: if a global JSON codec is ever registered (asyncpg
would then return a dict/list directly), it passes the value through untouched.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import Result


def scalar_json(result: Result) -> Any:
    """Return the single jsonb scalar of ``result`` as a Python object."""
    value = result.scalar_one()
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    return json.loads(value)

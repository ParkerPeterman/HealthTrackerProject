"""Make Python/numpy/pandas values safe for JSONB and JSON responses."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, dt.datetime | dt.date):
        return obj.isoformat()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if obj is not None and type(obj).__name__ in ("NAType", "NaTType"):
        return None
    return obj

"""
Schema-version registry.

Adding support for a future schema_version means writing a new
`validate(raw_json) -> ValidatedAnalysis` module and registering it here --
nothing else in the application changes.
"""
from __future__ import annotations

from app.errors import InvalidJsonError, UnsupportedSchemaVersionError
from app.json_validation import v1_0
from app.schemas.validated import ValidatedAnalysis

_REGISTRY = {
    "1.0": v1_0.validate,
}


def validate_analysis_json(raw_json: str | dict) -> ValidatedAnalysis:
    # Peek at schema_version without fully trusting the shape yet.
    if isinstance(raw_json, dict):
        version = raw_json.get("schema_version")
    else:
        peeked = v1_0.parse_and_check_shape(raw_json)
        version = peeked.get("schema_version")

    if version not in _REGISTRY:
        raise UnsupportedSchemaVersionError(
            f"schema_version '{version}' is not supported. Supported versions: {', '.join(_REGISTRY)}.",
            details={"schema_version": version, "supported_versions": list(_REGISTRY)},
        )
    return _REGISTRY[version](raw_json)

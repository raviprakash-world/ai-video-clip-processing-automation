"""
Schema-version registry.

Adding support for a future schema_version means writing a new
`validate(raw_json) -> ValidatedAnalysis` module and registering it here --
nothing else in the application changes.
"""
from __future__ import annotations

from app.errors import UnsupportedSchemaVersionError
from app.json_validation import v1_0
from app.json_validation.foreign_formats import translate_if_foreign
from app.schemas.validated import ValidatedAnalysis

_REGISTRY = {
    "1.0": v1_0.validate,
}


def validate_analysis_json(raw_json: str | dict) -> ValidatedAnalysis:
    data = v1_0.parse_json_only(raw_json)

    # If this isn't a native document at all (no schema_version, but matches a
    # known third-party tool's shape), translate it into one first. See
    # app.json_validation.foreign_formats for exactly what is and isn't
    # carried across in that translation.
    translated = translate_if_foreign(data)
    if translated is not None:
        data = translated

    version = data.get("schema_version")
    if version is None:
        # Neither a recognized foreign format nor a document that even claims a
        # schema_version -- let the native pipeline produce a precise "which
        # fields are missing" error instead of a vague "unsupported version".
        v1_0.parse_and_check_shape(data)  # always raises MissingRequiredFieldError here

    if version not in _REGISTRY:
        raise UnsupportedSchemaVersionError(
            f"schema_version '{version}' is not supported. Supported versions: {', '.join(_REGISTRY)}.",
            details={"schema_version": version, "supported_versions": list(_REGISTRY)},
        )
    return _REGISTRY[version](data)

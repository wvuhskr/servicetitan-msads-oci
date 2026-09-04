import json
from importlib import resources

from jsonschema import Draft202012Validator

_SCHEMA = json.loads(resources.files("st_msads_oci").joinpath("input.schema.json").read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)


def _field_names(schema):
    names = set()
    if isinstance(schema, dict):
        names.update(schema.get("properties", {}))
        for child in schema.values():
            names.update(_field_names(child))
    elif isinstance(schema, list):
        for child in schema:
            names.update(_field_names(child))
    return names


_FIELDS = _field_names(_SCHEMA)


class InputValidationError(ValueError):
    """Contains only sanitized field/rule descriptions from validate_input."""


def validate_input(payload) -> list:
    """Return human-readable errors ('$.jobs[0].completedOn: ...'); empty list means valid."""
    out = []
    for e in sorted(_VALIDATOR.iter_errors(payload), key=lambda e: str(list(e.absolute_path))):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int)
                              else f".{p}" if p in _FIELDS else "[key]"
                              for p in e.absolute_path)
        if e.validator == "required":
            # Names originate in our schema, never the untrusted instance.
            rule = "required fields: " + ", ".join(k for k in e.validator_value if k not in e.instance)
        elif e.validator == "type":
            rule = "expected type " + str(e.validator_value)
        else:
            rule = "failed " + e.validator + " validation"
        out.append(f"{path}: {rule}")
    return list(dict.fromkeys(out))

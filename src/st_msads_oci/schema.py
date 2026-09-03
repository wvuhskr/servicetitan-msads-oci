import json
from importlib import resources

from jsonschema import Draft202012Validator

_SCHEMA = json.loads(resources.files("st_msads_oci").joinpath("input.schema.json").read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)


def validate_input(payload) -> list:
    """Return human-readable errors ('$.jobs[0].completedOn: ...'); empty list means valid."""
    out = []
    for e in sorted(_VALIDATOR.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        out.append(f"{path}: {e.message}")
    return out

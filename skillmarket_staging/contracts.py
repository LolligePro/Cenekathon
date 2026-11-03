"""Wire contract v1.0, kept separate from the generic JSON staging schema."""

import json


class ContractError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ContractError(f"Non-standard JSON number: {value}")


def decode(data: bytes | str, location: str) -> dict:
    try:
        # Decode explicitly: JSONL/manifest must be UTF-8, not UTF-16/UTF-32.
        value = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data,
                           object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"Invalid UTF-8 JSON at {location}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"Expected a JSON object at {location}")
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractError(f"{name} must be a nonempty string without NUL")
    # In particular, never strip, case-fold or convert source IDs to numbers.
    return value


def _keys(row, required, optional=()):
    missing = set(required) - row.keys()
    extra = row.keys() - set(required) - set(optional)
    if missing or extra:
        raise ContractError(f"Contract fields: missing={sorted(missing)}, unexpected={sorted(extra)}")


def strings(value, name):
    if not isinstance(value, list):
        raise ContractError(f"{name} must be an array of strings")
    for item in value:
        text(item, name)
    return value


def validate_manifest(row):
    _keys(row, {"contract_version", "batch_id", "parser_version", "skill_taxonomy_version", "record_count"})
    if row["contract_version"] != "1.0":
        raise ContractError("Unsupported contract_version; expected 1.0")
    for key in ["batch_id", "parser_version", "skill_taxonomy_version"]:
        text(row[key], key)
    if type(row["record_count"]) is not int or not 0 < row["record_count"] <= 9223372036854775807:
        raise ContractError("record_count must be a positive signed 64-bit integer")


def validate_record(row, known_skills: set[str]):
    _keys(row, {"posting_id", "parse_status", "normalized_title", "skill_ids", "error"}, {"role_family"})
    text(row["posting_id"], "posting_id")
    skills = strings(row["skill_ids"], "skill_ids")
    if len(skills) != len(set(skills)):
        raise ContractError("skill_ids contains duplicate IDs")
    unknown = set(skills) - known_skills
    if unknown:
        raise ContractError(f"Unknown skill IDs: {sorted(unknown)[:5]}")
    family = row.get("role_family")
    if family is not None:
        text(family, "role_family")
        if family == "ALL":
            raise ContractError("role_family ALL is reserved")
    if row["parse_status"] == "ok":
        text(row["normalized_title"], "normalized_title")
        if row["error"] is not None:
            raise ContractError("An ok result must have error=null")
    elif row["parse_status"] == "failed":
        if row["normalized_title"] is not None or skills or family is not None:
            raise ContractError("A failed result requires null title/family and skill_ids=[]")
        error = row["error"]
        if not isinstance(error, dict):
            raise ContractError("A failed result requires an error object")
        _keys(error, {"code", "message"})
        if error["code"] not in ["EMPTY_INPUT", "MODEL_TIMEOUT", "INVALID_OUTPUT", "INTERNAL_ERROR"]:
            raise ContractError("Unknown parser error code")
        text(error["message"], "error.message")
    else:
        raise ContractError("parse_status must be ok or failed")


def validate_skill(row):
    _keys(row, {"skill_id", "name"}, {"category", "synonyms"})
    text(row["skill_id"], "skill_id")
    text(row["name"], "name")
    if row.get("category") is not None:
        text(row["category"], "category")
    strings(row.get("synonyms", []), "synonyms")

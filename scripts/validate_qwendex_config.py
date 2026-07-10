#!/usr/bin/env python3
"""Validate Qwendex's two published configs against their published schema."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any


DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"
DEPENDENCIES_PATH = Path("config/qwendex/dependencies.json")
SCHEMA_PATH = Path("config/qwendex/qwendex.schema.json")
PUBLISHED_CONFIG_PATHS = (
    Path("config/qwendex/qwendex.json"),
    Path("config/qwendex/qwendex.sample.json"),
)
RELEASE_PATH = Path("RELEASE.md")
SCHEMA_VERSION_RE = re.compile(r"qwendex\.config\.v[1-9][0-9]*\Z")
SEMVER_RE = re.compile(
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)


def issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def load_json(
    repo_root: Path,
    relative_path: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    path = repo_root / relative_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(issue("invalid_json", relative_path.as_posix(), str(exc)))
        return None
    if not isinstance(payload, dict):
        errors.append(
            issue(
                "invalid_json_root",
                relative_path.as_posix(),
                "published JSON document must be an object",
            )
        )
        return None
    return payload


def is_semver(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(
        not (identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"))
        for identifier in prerelease.split(".")
    )


def json_pointer(parts: Iterable[object]) -> str:
    encoded = []
    for part in parts:
        encoded.append(str(part).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(encoded) if encoded else "/"


def external_schema_references(
    value: object,
    path: tuple[object, ...] = (),
) -> list[tuple[str, object]]:
    references: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                references.append((json_pointer(child_path), child))
            references.extend(external_schema_references(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(external_schema_references(child, (*path, index)))
    return references


def validate_repository(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    errors: list[dict[str, str]] = []
    dependencies = load_json(repo_root, DEPENDENCIES_PATH, errors)
    schema = load_json(repo_root, SCHEMA_PATH, errors)
    configs = {
        path.as_posix(): load_json(repo_root, path, errors)
        for path in PUBLISHED_CONFIG_PATHS
    }

    required_jsonschema_version = ""
    if dependencies is not None:
        versions = dependencies.get("validation_python_module_versions")
        if not isinstance(versions, dict):
            errors.append(
                issue(
                    "dependency_pin_missing",
                    DEPENDENCIES_PATH.as_posix(),
                    "validation_python_module_versions must be an object",
                )
            )
        else:
            candidate = versions.get("jsonschema")
            if not isinstance(candidate, str) or not candidate:
                errors.append(
                    issue(
                        "dependency_pin_missing",
                        DEPENDENCIES_PATH.as_posix(),
                        "jsonschema must have an exact validation dependency pin",
                    )
                )
            else:
                required_jsonschema_version = candidate

    observed_jsonschema_version = ""
    try:
        observed_jsonschema_version = distribution_version("jsonschema")
    except PackageNotFoundError:
        errors.append(
            issue(
                "dependency_missing",
                "jsonschema",
                "install the exact jsonschema version declared in dependencies.json",
            )
        )
    if (
        required_jsonschema_version
        and observed_jsonschema_version
        and observed_jsonschema_version != required_jsonschema_version
    ):
        errors.append(
            issue(
                "dependency_version_mismatch",
                "jsonschema",
                f"installed {observed_jsonschema_version!r}; required {required_jsonschema_version!r}",
            )
        )

    validator_class: Any = None
    schema_error_class: type[Exception] = Exception
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError as exc:
        errors.append(issue("dependency_import_failed", "jsonschema", str(exc)))
    else:
        validator_class = Draft202012Validator
        schema_error_class = SchemaError

    schema_is_valid = False
    schema_has_external_refs = False
    schema_version = ""
    schema_id = ""
    if schema is not None:
        external_refs = external_schema_references(schema)
        schema_has_external_refs = bool(external_refs)
        for reference_path, reference in external_refs:
            errors.append(
                issue(
                    "unbounded_schema_reference",
                    f"{SCHEMA_PATH.as_posix()}#{reference_path}",
                    f"published schema references must be local fragments; got {reference!r}",
                )
            )
        if schema.get("$schema") != DRAFT_2020_12_URI:
            errors.append(
                issue(
                    "schema_draft_mismatch",
                    SCHEMA_PATH.as_posix(),
                    f"$schema must be {DRAFT_2020_12_URI!r}",
                )
            )
        schema_properties = schema.get("properties")
        schema_property = (
            schema_properties.get("schema_version", {})
            if isinstance(schema_properties, dict)
            else {}
        )
        if isinstance(schema_property, dict):
            candidate = schema_property.get("const")
            if isinstance(candidate, str):
                schema_version = candidate
        if not SCHEMA_VERSION_RE.fullmatch(schema_version):
            errors.append(
                issue(
                    "schema_version_contract",
                    SCHEMA_PATH.as_posix(),
                    "properties.schema_version.const must be qwendex.config.vN",
                )
            )
        schema_id_value = schema.get("$id")
        if isinstance(schema_id_value, str):
            schema_id = schema_id_value
        expected_schema_id = (
            f"https://qwendex.local/schema/{schema_version}.json" if schema_version else ""
        )
        if not expected_schema_id or schema_id != expected_schema_id:
            errors.append(
                issue(
                    "schema_id_mismatch",
                    SCHEMA_PATH.as_posix(),
                    f"$id must match the schema-version const ({expected_schema_id!r})",
                )
            )
        if validator_class is not None:
            try:
                validator_class.check_schema(schema)
            except schema_error_class as exc:
                errors.append(
                    issue(
                        "invalid_schema",
                        SCHEMA_PATH.as_posix(),
                        f"{json_pointer(exc.absolute_path)}: {exc.message}",
                    )
                )
            else:
                schema_is_valid = not schema_has_external_refs

    versions: dict[str, str] = {}
    config_schema_versions: dict[str, str] = {}
    if schema_is_valid and schema is not None and validator_class is not None:
        validator = validator_class(schema)
        for relative_path, config in configs.items():
            if config is None:
                continue
            for validation_error in sorted(
                validator.iter_errors(config),
                key=lambda item: json_pointer(item.absolute_path),
            ):
                errors.append(
                    issue(
                        "config_schema_violation",
                        f"{relative_path}#{json_pointer(validation_error.absolute_path)}",
                        validation_error.message,
                    )
                )

    for relative_path, config in configs.items():
        if config is None:
            continue
        version = config.get("version")
        if not is_semver(version):
            errors.append(
                issue(
                    "invalid_semver",
                    f"{relative_path}#/version",
                    f"version must be a SemVer value; got {version!r}",
                )
            )
        elif isinstance(version, str):
            versions[relative_path] = version
        config_schema_version = config.get("schema_version")
        if isinstance(config_schema_version, str):
            config_schema_versions[relative_path] = config_schema_version
        if schema_version and config_schema_version != schema_version:
            errors.append(
                issue(
                    "config_schema_version_mismatch",
                    f"{relative_path}#/schema_version",
                    f"expected {schema_version!r}; got {config_schema_version!r}",
                )
            )
        seats = config.get("seats")
        context = config.get("context")
        global_compact = context.get("compact_limit") if isinstance(context, dict) else None
        if isinstance(seats, dict):
            for seat_name, seat in seats.items():
                if not isinstance(seat, dict):
                    continue
                context_window = seat.get("context_window")
                compact_limit = seat.get("compact_limit", global_compact)
                if (
                    isinstance(context_window, int)
                    and not isinstance(context_window, bool)
                    and isinstance(compact_limit, int)
                    and not isinstance(compact_limit, bool)
                    and compact_limit >= context_window
                ):
                    errors.append(
                        issue(
                            "invalid_context_budget",
                            f"{relative_path}#/seats/{seat_name}/compact_limit",
                            "compact_limit must be lower than context_window",
                        )
                    )

    distinct_versions = set(versions.values())
    if len(versions) == len(PUBLISHED_CONFIG_PATHS) and len(distinct_versions) != 1:
        errors.append(
            issue(
                "published_version_mismatch",
                "config/qwendex",
                "qwendex.json and qwendex.sample.json must publish the same version",
            )
        )
    distinct_schema_versions = set(config_schema_versions.values())
    if (
        len(config_schema_versions) == len(PUBLISHED_CONFIG_PATHS)
        and len(distinct_schema_versions) != 1
    ):
        errors.append(
            issue(
                "published_schema_version_mismatch",
                "config/qwendex",
                "qwendex.json and qwendex.sample.json must publish the same schema version",
            )
        )

    release_version = ""
    release_file = repo_root / RELEASE_PATH
    try:
        first_line = release_file.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError) as exc:
        errors.append(issue("release_version_missing", RELEASE_PATH.as_posix(), str(exc)))
    else:
        release_match = re.fullmatch(r"# v([^\s]+)", first_line)
        if release_match is None or not is_semver(release_match.group(1)):
            errors.append(
                issue(
                    "release_version_invalid",
                    RELEASE_PATH.as_posix(),
                    "first heading must be '# v<SemVer>'",
                )
            )
        else:
            release_version = release_match.group(1)
            if distinct_versions and distinct_versions != {release_version}:
                errors.append(
                    issue(
                        "release_version_mismatch",
                        RELEASE_PATH.as_posix(),
                        f"release {release_version!r} does not match published config version(s)",
                    )
                )

    return {
        "schema_version": "qwendex.config_validation.v1",
        "status": "pass" if not errors else "blocked",
        "validator": "jsonschema.Draft202012Validator",
        "required_jsonschema_version": required_jsonschema_version,
        "observed_jsonschema_version": observed_jsonschema_version,
        "schema_draft": schema.get("$schema", "") if schema is not None else "",
        "schema_id": schema_id,
        "config_schema_version": schema_version,
        "release_version": release_version,
        "published_configs": list(configs),
        "errors": errors,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate published Qwendex configs with JSON Schema Draft 2020-12."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Qwendex repository root (defaults to the script's parent repository)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_repository(args.repo_root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["status"] == "pass":
        print(
            "Qwendex published config validation: pass "
            f"({len(result['published_configs'])} configs, Draft 2020-12)"
        )
    else:
        print("Qwendex published config validation: blocked", file=sys.stderr)
        for error in result["errors"]:
            print(
                f"- {error['code']}: {error['path']}: {error['message']}",
                file=sys.stderr,
            )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

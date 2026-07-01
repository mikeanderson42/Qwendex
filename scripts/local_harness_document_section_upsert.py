#!/usr/bin/env python3
"""CLI wrapper for the local-harness Markdown section upsert primitive."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from typing import Any

from artifact_queue_mcp import ToolError, tool_document_section_upsert


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="Trusted repository or artifact directory.")
    parser.add_argument("--file", required=True, help="Relative Markdown file path under --dir.")
    parser.add_argument("--section-title", required=True, help="Markdown heading text without # markers.")
    parser.add_argument("--body", default="", help="Section body. Prefer --body-b64 for generated content.")
    parser.add_argument("--body-b64", default="", help="Base64-encoded UTF-8 section body.")
    parser.add_argument("--level", type=int, default=2, help="Markdown heading level.")
    parser.add_argument("--item-number", type=int, default=None, help="Optional current item number.")
    parser.add_argument("--total-items", type=int, default=None, help="Optional total item count.")
    parser.add_argument("--min-bytes", type=int, default=1, help="Minimum artifact bytes after upsert.")
    parser.add_argument("--done-marker", default="DOCUMENT_SECTION_DONE", help="Marker printed when inserted/updated.")
    parser.add_argument(
        "--already-marker",
        default="DOCUMENT_SECTION_ALREADY_PRESENT",
        help="Marker printed when the section is already present.",
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON payload.")
    return parser.parse_args()


def decoded_body(args: argparse.Namespace) -> str:
    if args.body_b64:
        try:
            return base64.b64decode(args.body_b64.encode("ascii"), validate=True).decode("utf-8")
        except Exception as exc:
            raise ToolError(f"invalid --body-b64: {exc}") from exc
    return str(args.body or "")


def main() -> int:
    args = parse_args()
    payload_args: dict[str, Any] = {
        "dir": args.dir,
        "file": args.file,
        "section_title": args.section_title,
        "body": decoded_body(args),
        "level": args.level,
        "min_bytes": args.min_bytes,
    }
    if args.item_number is not None:
        payload_args["item_number"] = args.item_number
    if args.total_items is not None:
        payload_args["total_items"] = args.total_items
    try:
        payload = tool_document_section_upsert(payload_args)
    except Exception as exc:
        print(f"DOCUMENT_SECTION_ERROR {exc}", file=sys.stderr)
        return 1

    marker = args.already_marker if payload.get("action") == "already_present" else args.done_marker
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            marker,
            payload.get("file", ""),
            f"bytes={payload.get('bytes', '')}",
            f"action={payload.get('action', '')}",
            f"next_item={payload.get('next_item', '')}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

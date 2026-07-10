#!/usr/bin/env python3
"""Fail CI when the draft Fantrax executor lacks confirmation safety.

This is intentionally a source-contract gate, not proof that Fantrax's DOM is
unchanged. It prevents the executor's mock-only unit suite from being treated
as sufficient while stale/replayed confirmations and unverifiable writes are
still possible.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


REQUIRED_REQUEST_FIELDS = {
    "action",
    "player_id",
    "snapshot_id",
    "proposal_id",
    "input_hash",
    "confirm_player_name",
}

REQUIRED_ACTION_MARKERS = {
    "exact proposal binding": ("proposal_id", "input_hash", "snapshot_id", "slot_moves"),
    "fresh live preflight": ("preflight", "max_snapshot_age"),
    "post-write verification": ("post_write", "verify"),
}

REQUIRED_TEST_NAME_MARKERS = {
    "stale snapshot rejection": ("stale_snapshot",),
    "proposal hash mismatch rejection": ("proposal_hash", "input_hash"),
    "slot legality preflight": ("slot_legality", "illegal_slot"),
    "post-write verification failure": ("post_write", "verification_failure"),
    "protected dynasty asset": ("young_player", "protected_anchor", "dynasty_asset"),
}


def audit_executor_contract(source_root: Path) -> list[str]:
    failures: list[str] = []
    api_path = source_root / "sandlot_api.py"
    actions_path = source_root / "sandlot_actions.py"
    tests_path = source_root / "tests" / "test_sandlot_actions.py"

    for path in (api_path, actions_path, tests_path):
        if not path.is_file():
            failures.append(f"missing required executor file: {path.relative_to(source_root)}")
    if failures:
        return failures

    api_text = api_path.read_text(encoding="utf-8")
    actions_text = actions_path.read_text(encoding="utf-8").casefold()
    tests_text = tests_path.read_text(encoding="utf-8").casefold()

    request_fields = _class_fields(api_text, "ActionRequest")
    missing_fields = sorted(REQUIRED_REQUEST_FIELDS - request_fields)
    if missing_fields:
        failures.append(
            "ActionRequest is not bound to the exact confirmed proposal; missing fields: "
            + ", ".join(missing_fields)
        )

    for label, markers in REQUIRED_ACTION_MARKERS.items():
        missing = [marker for marker in markers if marker not in actions_text]
        if missing:
            failures.append(f"executor lacks {label}: missing " + ", ".join(missing))

    test_names = _test_function_names(tests_text)
    for label, alternatives in REQUIRED_TEST_NAME_MARKERS.items():
        if not any(marker in name for name in test_names for marker in alternatives):
            failures.append(
                f"executor tests lack {label} coverage; expected a test name containing one of: "
                + ", ".join(alternatives)
            )
    return failures


def _class_fields(source: str, class_name: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        fields: set[str] = set()
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields.add(child.target.id)
            elif isinstance(child, ast.Assign):
                fields.update(
                    target.id for target in child.targets if isinstance(target, ast.Name)
                )
        return fields
    return set()


def _test_function_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a checked-out Sandlot executor branch for required safety contracts."
    )
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args()
    failures = audit_executor_contract(args.source_root.resolve())
    if failures:
        print("Executor safety contract: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Executor safety contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

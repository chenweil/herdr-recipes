#!/usr/bin/env python3
"""Report Herdr agent availability without installing or changing integrations."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "config" / "agent-catalog.json"
VERSION_RE = re.compile(r"\b(\d+)\.(\d+)\.(\d+)")


def load_catalog(path):
    try:
        catalog = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read agent catalog {path}: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("kinds"), list):
        raise RuntimeError(f"invalid agent catalog {path}: kinds must be an array")
    return catalog


def run_command(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def parse_version(text):
    match = VERSION_RE.search(text)
    return tuple(int(part) for part in match.groups()) if match else None


def check_version(minimum):
    result = run_command(["herdr", "--version"])
    version = parse_version(result.stdout + "\n" + result.stderr)
    required = parse_version(minimum)
    if result.returncode != 0 or version is None or required is None:
        print("[herdr-recipes] error: unable to determine Herdr version (need %s+)" % minimum, file=sys.stderr)
        return False
    if version < required:
        print(
            "[herdr-recipes] error: Herdr %s is too old; Herdr %s or newer is required"
            % (".".join(str(part) for part in version), minimum),
            file=sys.stderr,
        )
        return False
    print(
        "[herdr-recipes] Herdr %s (minimum %s): ok"
        % (".".join(str(part) for part in version), minimum)
    )
    return True


def parse_supported_kinds(help_text):
    match = re.search(r"\[possible values:\s*(.*?)\]", help_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    return [
        value.strip()
        for value in match.group(1).replace("\n", " ").split(",")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", value.strip())
    ]


def parse_integration_status(text):
    statuses = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([^:]+):\s*(.*)$", line)
        if not match:
            continue
        target = match.group(1).strip()
        detail = match.group(2).strip().lower()
        if detail.startswith("current"):
            status = "current"
        elif detail.startswith("outdated"):
            status = "outdated"
        elif detail.startswith("not installed") or detail.startswith("missing"):
            status = "missing"
        else:
            status = "unknown"
        statuses[target] = status
    return statuses


def executable_report(entry):
    candidates = entry.get("executables", [])
    found = [candidate for candidate in candidates if shutil.which(candidate)]
    if found:
        return "found (%s)" % ", ".join(found)
    if candidates:
        return "missing (%s)" % ", ".join(candidates)
    return "unknown (no catalog candidate)"


def integration_report(entry, statuses):
    targets = entry.get("integration_targets", [])
    if not targets:
        return "N/A"
    for target in targets:
        if target in statuses:
            return "%s (%s)" % (statuses[target], target)
    return "unknown (%s)" % ", ".join(targets)


def audit(catalog):
    if not check_version(catalog.get("min_herdr_version", "0.8.2")):
        return 2

    help_result = run_command(["herdr", "agent", "start", "--help"])
    supported = parse_supported_kinds(help_result.stdout + "\n" + help_result.stderr)
    if help_result.returncode != 0 or not supported:
        print(
            "[herdr-recipes] warning: could not parse herdr agent start --help; "
            "falling back to the versioned catalog",
            file=sys.stderr,
        )
        supported = [entry["kind"] for entry in catalog["kinds"]]

    integration_result = run_command(["herdr", "integration", "status"])
    if integration_result.returncode != 0:
        print(
            "[herdr-recipes] warning: could not read herdr integration status; "
            "integration status is unknown",
            file=sys.stderr,
        )
        integration_status = {}
    else:
        integration_status = parse_integration_status(
            integration_result.stdout + "\n" + integration_result.stderr
        )

    catalog_by_kind = {entry["kind"]: entry for entry in catalog["kinds"]}
    print("[herdr-recipes] agent audit (catalog v%s)" % catalog.get("catalog_version", "unknown"))
    for kind in supported:
        entry = catalog_by_kind.get(kind)
        if entry is None:
            print(f"{kind}: availability unknown (not in catalog)")
            continue
        print(
            "%s: executable=%s; integration=%s"
            % (kind, executable_report(entry), integration_report(entry, integration_status))
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--check-version", action="store_true")
    args = parser.parse_args()
    try:
        catalog = load_catalog(args.catalog)
        if args.check_version:
            return 0 if check_version(catalog.get("min_herdr_version", "0.8.2")) else 2
        return audit(catalog)
    except RuntimeError as exc:
        print(f"[herdr-recipes] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

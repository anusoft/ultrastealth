"""Load and validate the marketplace deployment manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).with_name("marketplaces.json")
REQUIRED_SITES = frozenset(
    {
        "advice",
        "allonline",
        "b2s",
        "bigc",
        "bnbhome",
        "boots",
        "central",
        "dohome",
        "globalhouse",
        "gourmetmarket",
        "ihavecpu",
        "jib",
        "lotuss",
        "makro",
        "ofm",
        "powerbuy",
        "supersports",
        "thaiwatsadu",
        "tops",
        "villamarket",
        "watsons",
    }
)
REQUIRED_FIELDS = frozenset(
    {
        "slug",
        "entrypoint",
        "smoke_args",
        "full_args",
        "interval_seconds",
        "priority",
        "enabled",
    }
)


def load_manifest(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return a validated slug-to-site mapping."""
    manifest_path = path or MANIFEST_PATH
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("marketplace manifest must be an object")
    if set(data) != REQUIRED_SITES:
        missing = sorted(REQUIRED_SITES - set(data))
        extra = sorted(set(data) - REQUIRED_SITES)
        raise ValueError(f"marketplace mismatch: missing={missing}, extra={extra}")
    for slug, site in data.items():
        if not isinstance(site, dict):
            raise ValueError(f"marketplace {slug} must be an object")
        missing_fields = REQUIRED_FIELDS - set(site)
        if missing_fields:
            raise ValueError(f"marketplace {slug} missing {sorted(missing_fields)}")
        if site["slug"] != slug:
            raise ValueError(f"marketplace key {slug} disagrees with slug {site['slug']}")
        if not isinstance(site["interval_seconds"], int) or site["interval_seconds"] < 1:
            raise ValueError(f"marketplace {slug} interval_seconds must be positive")
        for field in ("smoke_args", "full_args"):
            if not isinstance(site[field], list) or not all(
                isinstance(value, str) for value in site[field]
            ):
                raise ValueError(f"marketplace {slug} {field} must be strings")
    return data


def run_args(site: dict[str, Any], mode: str) -> list[str]:
    """Return a copy of the configured arguments for a crawl mode."""
    if mode not in {"smoke", "full"}:
        raise ValueError(f"unsupported crawl mode: {mode}")
    return list(site[f"{mode}_args"])

"""Classify and normalize lossless marketplace crawl documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


VOLATILE_KEYS = frozenset(
    {
        "capturedAt",
        "requestTimestamp",
        "runId",
        "scrapedAt",
        "timestamp",
    }
)


def classify_path(relative_path: str) -> str:
    """Classify a crawler output path into a stable document kind."""
    path = Path(relative_path)
    parts = path.parts
    if "products" in parts:
        return "product"
    if "reviews" in parts:
        return "review_page"
    if "category-pages" in parts or ("categories" in parts and len(parts) > 1):
        return "category_page"
    return {
        "categories.json": "category",
        "metadata.json": "metadata",
        "products-index.json": "index",
        "run-summary.json": "summary",
        "summary.json": "summary",
    }.get(path.name, "other")


def raw_digest(content: bytes) -> str:
    """Return the SHA-256 for the exact file bytes."""
    return hashlib.sha256(content).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical(child)
            for key, child in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_canonical(child) for child in value]
    return value


def canonical_digest(payload: Any) -> str:
    """Hash business data after removing volatile capture fields."""
    encoded = json.dumps(
        _canonical(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    if isinstance(value, dict):
        value = _first(value.get("value"), value.get("amount"), value.get("price"))
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        parsed = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _brand(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        candidate = _first(
            value.get("name"),
            value.get("displayName"),
            value.get("title"),
            value.get("code"),
        )
        return str(candidate) if candidate is not None else None
    return None


def _category_names(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in values:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = _first(
                item.get("name"),
                item.get("title"),
                item.get("slug"),
                item.get("code"),
                item.get("id"),
            )
            if name is not None:
                names.append(str(name))
    return list(dict.fromkeys(names))


def _image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value else []
    urls: list[str] = []
    for image in value:
        if isinstance(image, str):
            candidate = image
        elif isinstance(image, dict):
            candidate = _first(
                image.get("url"),
                image.get("thumbnailUrl"),
                image.get("src"),
                image.get("image"),
            )
        else:
            candidate = None
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            urls.append(candidate)
    return list(dict.fromkeys(urls))


def product_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract common query fields without discarding the source payload."""
    identity = _mapping(payload.get("identity"))
    pricing = _mapping(payload.get("pricing"))
    price = _mapping(payload.get("price"))
    old_price = _mapping(payload.get("oldPrice"))
    reviews = _mapping(payload.get("reviews"))

    source_id = _first(
        payload.get("id"),
        payload.get("sku"),
        payload.get("code"),
        payload.get("handle"),
        identity.get("id"),
        identity.get("sku"),
        identity.get("code"),
        identity.get("productId"),
        identity.get("prcode"),
    )
    if source_id is None:
        raise ValueError("product has no stable product identifier")

    categories = _first(
        payload.get("categories"),
        payload.get("categoryPath"),
        payload.get("category"),
    )
    current_price = _number(
        _first(
            pricing.get("current"),
            pricing.get("price"),
            pricing.get("sale"),
            payload.get("salePrice"),
            payload.get("price"),
        )
    )
    regular_price = _number(
        _first(
            pricing.get("regular"),
            pricing.get("originalPrice"),
            payload.get("regularPrice"),
            payload.get("oldPrice"),
        )
    )
    rating = _number(
        _first(
            payload.get("averageRating"),
            payload.get("rating"),
            reviews.get("ratingValue"),
            _mapping(reviews.get("summary")).get("average"),
        )
    )
    review_count = _number(
        _first(
            payload.get("reviewCountHint"),
            payload.get("reviewCount"),
            reviews.get("reviewCount"),
            reviews.get("total"),
        )
    )

    return {
        "source_product_id": str(source_id),
        "sku": str(_first(payload.get("sku"), identity.get("sku"), source_id)),
        "title": _first(
            payload.get("name"),
            payload.get("title"),
            identity.get("name"),
            identity.get("title"),
            identity.get("productName"),
        ),
        "brand": _brand(_first(payload.get("brand"), identity.get("brand"))),
        "source_url": _first(payload.get("url"), identity.get("url")),
        "current_price": current_price,
        "regular_price": regular_price,
        "currency": _first(
            pricing.get("currency"),
            price.get("currency"),
            old_price.get("currency"),
        ),
        "availability": payload.get("availability", payload.get("stock")),
        "category_path": _category_names(categories),
        "image_urls": _image_urls(payload.get("images")),
        "rating": rating,
        "review_count": int(review_count) if review_count is not None else None,
        "canonical_sha256": canonical_digest(payload),
    }

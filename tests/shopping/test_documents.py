import json
import unittest

from shopping_app.documents import (
    canonical_digest,
    classify_path,
    product_projection,
    raw_digest,
)


class DocumentTests(unittest.TestCase):
    def test_path_classification_covers_crawler_outputs(self):
        cases = {
            "products/BP_288766.json": "product",
            "reviews/BP_288766-page-0000.json": "review_page",
            "category-pages/skin/page-0001.json": "category_page",
            "categories/electronics/page-0001.json": "category_page",
            "categories.json": "category",
            "summary.json": "summary",
            "run-summary.json": "summary",
            "metadata.json": "metadata",
            "products-index.json": "index",
            "root-search.json": "other",
        }

        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_path(path), expected)

    def test_raw_digest_covers_exact_bytes(self):
        compact = b'{"id":"1"}'
        indented = b'{\n  "id": "1"\n}\n'

        self.assertNotEqual(raw_digest(compact), raw_digest(indented))

    def test_volatile_capture_fields_do_not_change_revision_digest(self):
        first = {
            "id": "1",
            "name": "Item",
            "scrapedAt": "2026-07-15T01:00:00Z",
            "raw": {"requestTimestamp": 1},
        }
        second = {
            "id": "1",
            "name": "Item",
            "scrapedAt": "2026-07-15T02:00:00Z",
            "raw": {"requestTimestamp": 2},
        }

        self.assertEqual(canonical_digest(first), canonical_digest(second))

    def test_business_change_creates_a_new_revision_digest(self):
        first = {"id": "1", "pricing": {"current": 10}}
        second = {"id": "1", "pricing": {"current": 11}}

        self.assertNotEqual(canonical_digest(first), canonical_digest(second))

    def test_standard_product_is_projected(self):
        payload = {
            "id": "item-1",
            "sku": "SKU-1",
            "name": "Item",
            "brand": "ACME",
            "url": "https://example.test/item-1",
            "pricing": {"currency": "THB", "current": 99, "regular": 120},
            "availability": {"status": "inStock"},
            "categories": ["Home", "Kitchen"],
            "images": ["https://example.test/1.jpg"],
            "reviews": {"ratingValue": 4.5, "reviewCount": 8},
        }

        product = product_projection(payload)

        self.assertEqual(product["source_product_id"], "item-1")
        self.assertEqual(product["current_price"], 99)
        self.assertEqual(product["category_path"], ["Home", "Kitchen"])
        self.assertEqual(product["image_urls"], ["https://example.test/1.jpg"])

    def test_powerbuy_nested_identity_and_images_are_projected(self):
        payload = {
            "identity": {
                "sku": "310044",
                "title": "Sound bar",
                "brand": "MARSHALL",
            },
            "pricing": {"currency": "THB", "price": 25900},
            "category": {"name": "TV & Entertainment"},
            "images": [
                {
                    "thumbnailUrl": "https://example.test/310044.jpg",
                    "path": "310044.jpg",
                }
            ],
            "url": "https://example.test/310044",
        }

        product = product_projection(payload)

        self.assertEqual(product["source_product_id"], "310044")
        self.assertEqual(product["title"], "Sound bar")
        self.assertEqual(product["brand"], "MARSHALL")
        self.assertEqual(product["current_price"], 25900)
        self.assertEqual(product["image_urls"], ["https://example.test/310044.jpg"])

    def test_watsons_value_price_and_category_objects_are_projected(self):
        payload = {
            "code": "BP_1",
            "name": "Serum",
            "brand": "Brand",
            "price": {"value": 189, "currency": "THB"},
            "categoryPath": [{"name": "Skin"}, {"name": "Sun"}],
            "averageRating": 4.9,
            "reviewCountHint": 50,
        }

        product = product_projection(payload)

        self.assertEqual(product["source_product_id"], "BP_1")
        self.assertEqual(product["current_price"], 189)
        self.assertEqual(product["category_path"], ["Skin", "Sun"])
        self.assertEqual(product["rating"], 4.9)
        self.assertEqual(product["review_count"], 50)

    def test_missing_stable_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stable product identifier"):
            product_projection({"name": "Nameless identity"})

    def test_projection_is_json_serializable(self):
        projection = product_projection({"id": 1, "name": "Item"})

        json.dumps(projection)


if __name__ == "__main__":
    unittest.main()

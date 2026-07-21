# Shopping marketplace deployment implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy all 21 marketplace crawlers and existing marketplace artifacts to a lossless, queryable, resumable Hetzner service with portable PostgreSQL backfill exports.

**Architecture:** Python commands manage manifests, crawl lifecycles, PostgreSQL ingestion, scheduling, health, and exports. Existing Bun crawlers keep writing JSON files; the importer stores exact documents by SHA-256 and updates a common product projection. Systemd runs one due full crawl at a time, and migration-led exports support offline on-premises restoration.

**Tech Stack:** Python 3.12, `psycopg` 3, PostgreSQL 17, Bun, `scrapling-js`, systemd, `pg_dump`, zstd, rsync

---

## File structure

- Create `shopping_app/__init__.py`: package marker and version
- Create `shopping_app/manifest.py`: validate marketplace definitions and produce smoke or full arguments
- Create `shopping_app/documents.py`: classify JSON paths, calculate digests, canonicalize product payloads, and extract projections
- Create `shopping_app/database.py`: PostgreSQL connection, run lifecycle, lossless import, projection, and scheduler queries
- Create `shopping_app/runner.py`: disk guard, file locks, partial-run resume, Bun execution, atomic finalization, and ingestion
- Create `shopping_app/exporter.py`: baseline dump, incremental bundle, manifest, and checksum operations
- Create `shopping_app/cli.py`: command-line interface for migrate, ingest, crawl, schedule, health, baseline export, and incremental export
- Create `shopping_app/marketplaces.json`: exact 21-site deployment manifest
- Create `shopping_app/requirements.txt`: runtime dependency constraint
- Create `shopping_app/db/migrations/001_initial.sql`: application schema, constraints, grants, and indexes
- Create `shopping_app/db/seeds/001_marketplaces.sql`: idempotent 21-site seed
- Create `shopping_app/systemd/shopping-scheduler.service`: one-shot scheduler service
- Create `shopping_app/systemd/shopping-scheduler.timer`: 15-minute timer
- Create `shopping_app/systemd/shopping-crawl@.service`: manual per-marketplace service
- Create `shopping_app/deploy/bootstrap-remote.sh`: remote account, directory, virtual environment, database, dependency, migration, and unit bootstrap
- Create `tests/shopping/test_manifest.py`: manifest and argument tests
- Create `tests/shopping/test_documents.py`: classification, identity, projection, and canonical digest tests
- Create `tests/shopping/test_exporter.py`: watermark and export manifest tests
- Create `tests/shopping/test_migrations.py`: migration and seed content tests
- Modify `out/watsons/watsons.mjs`: make zero an explicit unlimited value for full crawl limits
- Create `tests/test_watsons_full_args.py`: Watsons zero-limit parser contract test

### Task 1: Validate the 21-site manifest

**Files:**
- Create: `shopping_app/__init__.py`
- Create: `shopping_app/marketplaces.json`
- Create: `shopping_app/manifest.py`
- Test: `tests/shopping/test_manifest.py`

- [ ] **Step 1: Write the failing manifest tests**

```python
import unittest
from shopping_app.manifest import load_manifest, run_args

class ManifestTests(unittest.TestCase):
    def test_manifest_has_exact_marketplaces(self):
        manifest = load_manifest()
        self.assertEqual(len(manifest), 21)
        self.assertEqual(set(manifest), {"advice", "allonline", "b2s", "bigc", "bnbhome", "boots", "central", "dohome", "globalhouse", "gourmetmarket", "ihavecpu", "jib", "lotuss", "makro", "ofm", "powerbuy", "supersports", "thaiwatsadu", "tops", "villamarket", "watsons"})

    def test_full_args_are_unlimited_and_resumable(self):
        args = run_args(load_manifest()["watsons"], "full")
        self.assertIn("--resume", args)
        for flag in ("--category-limit", "--page-limit", "--product-limit", "--review-page-limit", "--review-limit"):
            self.assertEqual(args[args.index(flag) + 1], "0")
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `python3 -m unittest tests.shopping.test_manifest -v`

Expected: `ModuleNotFoundError: No module named 'shopping_app'`

- [ ] **Step 3: Implement strict manifest loading and argument generation**

```python
REQUIRED_SITES = frozenset({"advice", "allonline", "b2s", "bigc", "bnbhome", "boots", "central", "dohome", "globalhouse", "gourmetmarket", "ihavecpu", "jib", "lotuss", "makro", "ofm", "powerbuy", "supersports", "thaiwatsadu", "tops", "villamarket", "watsons"})

def run_args(site: dict, mode: str) -> list[str]:
    if mode not in {"smoke", "full"}:
        raise ValueError(f"unsupported crawl mode: {mode}")
    return [str(value) for value in site[f"{mode}_args"]]
```

The JSON entry for each site must contain `entrypoint`, `smoke_args`, `full_args`, `interval_seconds`, `priority`, and `enabled`. Full arguments use zero limits plus `--resume`; smoke arguments use one category, page, and product.

- [ ] **Step 4: Run manifest tests**

Run: `python3 -m unittest tests.shopping.test_manifest -v`

Expected: all manifest tests pass.

- [ ] **Step 5: Commit the manifest unit**

```bash
git add shopping_app/__init__.py shopping_app/marketplaces.json shopping_app/manifest.py tests/shopping/test_manifest.py
git commit -m "feat: define shopping marketplace manifest"
```

### Task 2: Build lossless document classification and projections

**Files:**
- Create: `shopping_app/documents.py`
- Test: `tests/shopping/test_documents.py`

- [ ] **Step 1: Write failing document tests**

```python
import unittest
from shopping_app.documents import canonical_digest, classify_path, product_projection

class DocumentTests(unittest.TestCase):
    def test_product_path_classifies_as_product(self):
        self.assertEqual(classify_path("products/BP_288766.json"), "product")

    def test_powerbuy_identity_is_projected(self):
        payload = {"identity": {"id": "310044", "sku": "SKU-1", "name": "TV", "brand": "ACME"}, "pricing": {"current": 999.0}, "url": "https://example.test/p/310044"}
        product = product_projection(payload)
        self.assertEqual(product["source_product_id"], "310044")
        self.assertEqual(product["current_price"], 999.0)

    def test_volatile_capture_time_does_not_change_revision(self):
        first = {"id": "1", "name": "Item", "scrapedAt": "2026-07-15T01:00:00Z"}
        second = {"id": "1", "name": "Item", "scrapedAt": "2026-07-15T02:00:00Z"}
        self.assertEqual(canonical_digest(first), canonical_digest(second))
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `python3 -m unittest tests.shopping.test_documents -v`

Expected: import fails because `shopping_app.documents` does not exist.

- [ ] **Step 3: Implement document helpers**

```python
VOLATILE_KEYS = frozenset({"scrapedAt", "capturedAt", "runId", "requestTimestamp", "timestamp"})

def classify_path(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if "products" in parts:
        return "product"
    if "reviews" in parts:
        return "review_page"
    if "category-pages" in parts or "categories" in parts and relative_path.endswith(".json"):
        return "category_page"
    name = Path(relative_path).name
    return {"summary.json": "summary", "run-summary.json": "summary", "metadata.json": "metadata", "categories.json": "category", "products-index.json": "index"}.get(name, "other")
```

Implement recursive canonicalization with sorted keys, exact raw-byte SHA-256, stable identity fallback through `id`, `sku`, `code`, and nested `identity`, plus common price, availability, category, image, rating, and review fields.

- [ ] **Step 4: Run document tests**

Run: `python3 -m unittest tests.shopping.test_documents -v`

Expected: all document tests pass.

- [ ] **Step 5: Commit document processing**

```bash
git add shopping_app/documents.py tests/shopping/test_documents.py
git commit -m "feat: normalize shopping crawl documents"
```

### Task 3: Add versioned PostgreSQL schema and seeds

**Files:**
- Create: `shopping_app/db/migrations/001_initial.sql`
- Create: `shopping_app/db/seeds/001_marketplaces.sql`
- Create: `shopping_app/migrations.py`
- Create: `shopping_app/requirements.txt`
- Test: `tests/shopping/test_migrations.py`

- [ ] **Step 1: Write failing migration contract tests**

```python
import unittest
from pathlib import Path

class MigrationTests(unittest.TestCase):
    def test_initial_schema_contains_lossless_and_projection_tables(self):
        sql = Path("shopping_app/db/migrations/001_initial.sql").read_text()
        for table in ("schema_migrations", "marketplaces", "crawl_runs", "crawl_errors", "document_blobs", "crawl_documents", "products_current", "product_revisions", "export_batches", "imported_batches"):
            self.assertIn(f"CREATE TABLE shopping.{table}", sql)

    def test_seed_contains_all_sites_and_is_idempotent(self):
        sql = Path("shopping_app/db/seeds/001_marketplaces.sql").read_text()
        self.assertEqual(sql.count("interval '7 days'"), 21)
        self.assertIn("ON CONFLICT (slug) DO UPDATE", sql)
```

- [ ] **Step 2: Run tests and verify missing file failure**

Run: `python3 -m unittest tests.shopping.test_migrations -v`

Expected: `FileNotFoundError` for the initial migration.

- [ ] **Step 3: Implement schema, grants, seed, and migration checksums**

```sql
CREATE SCHEMA IF NOT EXISTS shopping AUTHORIZATION shopping_owner;
CREATE TABLE shopping.document_blobs (
  content_sha256 text PRIMARY KEY CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  payload jsonb NOT NULL,
  byte_count bigint NOT NULL CHECK (byte_count >= 0),
  first_seen_at timestamptz NOT NULL DEFAULT now()
);
```

Create all tables from the design, use foreign keys with explicit delete behavior, add the required unique and query indexes, and grant only sequence use plus table DML to the runtime role. `migrations.py` computes each file SHA-256, rejects a changed applied migration, applies unapplied files in order, and runs seeds after migrations.

- [ ] **Step 4: Run migration contract tests**

Run: `python3 -m unittest tests.shopping.test_migrations -v`

Expected: all migration tests pass.

- [ ] **Step 5: Commit database definition**

```bash
git add shopping_app/db shopping_app/migrations.py shopping_app/requirements.txt tests/shopping/test_migrations.py
git commit -m "feat: add shopping database migrations"
```

### Task 4: Implement idempotent PostgreSQL ingestion

**Files:**
- Create: `shopping_app/database.py`
- Create: `shopping_app/cli.py`
- Test: `tests/shopping/test_database_contract.py`

- [ ] **Step 1: Write the failing database command contract test**

```python
import unittest
from unittest.mock import Mock
from shopping_app.database import import_document

class DatabaseContractTests(unittest.TestCase):
    def test_import_uses_exact_blob_and_run_path_identity(self):
        connection = Mock()
        payload = b'{"id":"1","name":"Item"}\n'
        import_document(connection, "run-id", "advice", "products/1.json", payload)
        statements = "\n".join(call.args[0] for call in connection.execute.call_args_list)
        self.assertIn("INSERT INTO shopping.document_blobs", statements)
        self.assertIn("INSERT INTO shopping.crawl_documents", statements)
        self.assertIn("INSERT INTO shopping.products_current", statements)
```

- [ ] **Step 2: Run tests and verify missing import function failure**

Run: `python3 -m unittest tests.shopping.test_database_contract -v`

Expected: import fails because `shopping_app.database` does not exist.

- [ ] **Step 3: Implement connection and import transactions**

```python
def connect(database_url: str | None = None):
    return psycopg.connect(database_url or os.environ.get("SHOPPING_DATABASE_URL", "dbname=shopping host=/var/run/postgresql"), autocommit=False)
```

Implement run creation and state changes, marketplace lookup, one-transaction-per-file blob insertion, document upsert, canonical revision insertion, current projection upsert, structured projection errors, directory import, due-source selection with `FOR UPDATE SKIP LOCKED`, success scheduling, bounded failure backoff, and health aggregates.

- [ ] **Step 4: Run database contract tests**

Run: `python3 -m unittest tests.shopping.test_database_contract -v`

Expected: all tests pass.

- [ ] **Step 5: Commit ingestion**

```bash
git add shopping_app/database.py shopping_app/cli.py tests/shopping/test_database_contract.py
git commit -m "feat: ingest shopping crawl data"
```

### Task 5: Make crawl execution resumable and schedulable

**Files:**
- Create: `shopping_app/runner.py`
- Modify: `shopping_app/cli.py`
- Test: `tests/shopping/test_runner.py`

- [ ] **Step 1: Write failing runner tests**

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from shopping_app.runner import ensure_disk_space, partial_directory

class RunnerTests(unittest.TestCase):
    def test_partial_directory_reuses_current_run(self):
        with TemporaryDirectory() as root:
            first = partial_directory(Path(root), "advice")
            second = partial_directory(Path(root), "advice")
            self.assertEqual(first, second)

    def test_disk_guard_rejects_less_than_ten_gib(self):
        with self.assertRaises(RuntimeError):
            ensure_disk_space(9 * 1024**3)
```

- [ ] **Step 2: Run tests and verify missing runner failure**

Run: `python3 -m unittest tests.shopping.test_runner -v`

Expected: import fails because `shopping_app.runner` does not exist.

- [ ] **Step 3: Implement crawl lifecycle and scheduler command**

```python
MIN_FREE_BYTES = 10 * 1024**3

def ensure_disk_space(free_bytes: int) -> None:
    if free_bytes < MIN_FREE_BYTES:
        raise RuntimeError(f"crawl requires 10 GiB free; found {free_bytes} bytes")
```

Use `fcntl.flock`, a stable `current.json` run identifier, explicit manifest arguments, `subprocess.run` with Bun, failure state recording, successful `run-manifest.json`, same-filesystem rename, directory ingestion, and scheduler selection of one due marketplace.

- [ ] **Step 4: Run runner tests**

Run: `python3 -m unittest tests.shopping.test_runner -v`

Expected: all tests pass.

- [ ] **Step 5: Commit runner and scheduler**

```bash
git add shopping_app/runner.py shopping_app/cli.py tests/shopping/test_runner.py
git commit -m "feat: run resumable shopping crawls"
```

### Task 6: Add baseline and incremental exports

**Files:**
- Create: `shopping_app/exporter.py`
- Modify: `shopping_app/cli.py`
- Test: `tests/shopping/test_exporter.py`

- [ ] **Step 1: Write failing export tests**

```python
import unittest
from shopping_app.exporter import export_manifest

class ExporterTests(unittest.TestCase):
    def test_manifest_records_watermarks_and_counts(self):
        manifest = export_manifest("batch-id", 10, 20, {"crawl_documents": 11})
        self.assertEqual(manifest["lower_document_id"], 10)
        self.assertEqual(manifest["upper_document_id"], 20)
        self.assertEqual(manifest["counts"]["crawl_documents"], 11)

    def test_repeated_bundle_is_reported_as_already_imported(self):
        imported = {"batch-id"}
        self.assertEqual(bundle_import_state("batch-id", imported), "already_imported")
```

- [ ] **Step 2: Run tests and verify missing exporter failure**

Run: `python3 -m unittest tests.shopping.test_exporter -v`

Expected: import fails because `shopping_app.exporter` does not exist.

- [ ] **Step 3: Implement verified exports**

```python
def export_manifest(batch_id: str, lower_document_id: int, upper_document_id: int, counts: dict[str, int]) -> dict:
    return {"batch_id": batch_id, "lower_document_id": lower_document_id, "upper_document_id": upper_document_id, "counts": counts, "created_at": datetime.now(timezone.utc).isoformat()}
```

Run `pg_dump --format=custom` for baseline exports. Incremental export writes migration checksums and newline-delimited JSON streams for runs, blobs, documents, revisions, projections, and errors, compresses them with zstd, records SHA-256 values, and marks `export_batches` complete only after verification. Implement `import-bundle` to verify the manifest and stream checksums, apply migrations, load rows through conflict-safe keys, update projections, and record the batch UUID in `imported_batches`; a repeated batch returns `already_imported`.

- [ ] **Step 4: Run export tests**

Run: `python3 -m unittest tests.shopping.test_exporter -v`

Expected: all tests pass.

- [ ] **Step 5: Commit export support**

```bash
git add shopping_app/exporter.py shopping_app/cli.py tests/shopping/test_exporter.py
git commit -m "feat: export shopping database backfills"
```

### Task 7: Fix Watsons full-catalog semantics

**Files:**
- Modify: `out/watsons/watsons.mjs`
- Create: `tests/test_watsons_full_args.py`

- [ ] **Step 1: Write a failing source contract test**

```python
import unittest
from pathlib import Path

class WatsonsFullArgsTests(unittest.TestCase):
    def test_zero_uses_non_negative_parser_for_all_limits(self):
        source = Path("out/watsons/watsons.mjs").read_text()
        for flag in ("categoryLimit", "pageLimit", "productLimit", "reviewPageLimit"):
            self.assertIn(f"opts.{flag} = nonNegativeInt", source)
        self.assertIn('raw === "all" || raw === "0"', source)
```

- [ ] **Step 2: Run test and verify it fails on positive-only parsing**

Run: `python3 -m unittest tests.test_watsons_full_args -v`

Expected: assertions fail because Watsons rejects zero.

- [ ] **Step 3: Implement zero-as-unlimited parsing and loops**

Use the shared `nonNegativeInt` semantics for category, page, product, and review-page limits. Treat `--review-limit 0` and `all` as unlimited. Replace fixed `slice(0, limit)` and bounded loop expressions with zero-aware selected counts and source pagination totals.

- [ ] **Step 4: Run Watsons and generated crawler tests**

Run: `python3 -m unittest tests.test_watsons_full_args -v && node --check out/watsons/watsons.mjs`

Expected: both commands pass.

- [ ] **Step 5: Commit Watsons semantics**

```bash
git add out/watsons/watsons.mjs tests/test_watsons_full_args.py
git commit -m "fix: support full Watsons catalog crawl"
```

### Task 8: Add systemd and remote bootstrap artifacts

**Files:**
- Create: `shopping_app/systemd/shopping-scheduler.service`
- Create: `shopping_app/systemd/shopping-scheduler.timer`
- Create: `shopping_app/systemd/shopping-crawl@.service`
- Create: `shopping_app/deploy/bootstrap-remote.sh`
- Test: `tests/shopping/test_deployment_files.py`

- [ ] **Step 1: Write failing deployment file tests**

```python
import unittest
from pathlib import Path

class DeploymentFileTests(unittest.TestCase):
    def test_services_run_as_restricted_account(self):
        for name in ("shopping-scheduler.service", "shopping-crawl@.service"):
            text = Path("shopping_app/systemd", name).read_text()
            self.assertIn("User=shopping", text)
            self.assertIn("NoNewPrivileges=true", text)
```

- [ ] **Step 2: Run tests and verify files are missing**

Run: `python3 -m unittest tests.shopping.test_deployment_files -v`

Expected: `FileNotFoundError` for the systemd unit.

- [ ] **Step 3: Implement hardened units and idempotent bootstrap**

```ini
[Service]
Type=oneshot
User=shopping
Group=shopping
WorkingDirectory=/home/anu/shopping/app
Environment=SHOPPING_ROOT=/home/anu/shopping
Environment=SHOPPING_DATABASE_URL=dbname=shopping host=/var/run/postgresql
ExecStart=/home/anu/shopping/app/.venv/bin/python -m shopping_app.cli schedule
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadOnlyPaths=/home/anu/shopping/app
ReadWritePaths=/home/anu/shopping
```

The bootstrap creates the Unix account and directory permissions, grants the runtime account execute-only traversal through `/home/anu`, installs Bun under `/var/lib/shopping/.bun`, creates `shopping_owner` and `shopping` database roles plus the database, creates the Python virtual environment, installs dependencies, runs migrations as PostgreSQL superuser, installs units, reloads systemd, and leaves the scheduler disabled until smoke validation passes.

- [ ] **Step 4: Run deployment file tests and shell syntax check**

Run: `python3 -m unittest tests.shopping.test_deployment_files -v && bash -n shopping_app/deploy/bootstrap-remote.sh`

Expected: all tests and syntax checks pass.

- [ ] **Step 5: Commit deployment artifacts**

```bash
git add shopping_app/systemd shopping_app/deploy tests/shopping/test_deployment_files.py
git commit -m "feat: deploy shopping crawler services"
```

### Task 9: Run the complete local verification suite

**Files:**
- Verify: `shopping_app/**`
- Verify: `tests/shopping/**`
- Verify: `out/*/*.mjs`

- [ ] **Step 1: Run focused shopping tests**

Run: `python3 -m unittest discover -s tests/shopping -v`

Expected: all shopping tests pass with no warnings.

- [ ] **Step 2: Run Watsons contract test**

Run: `python3 -m unittest tests.test_watsons_full_args -v`

Expected: all Watsons tests pass.

- [ ] **Step 3: Parse all marketplace entrypoints**

Run: `for file in out/{advice,allonline,b2s,bigc,bnbhome,boots,central,dohome,globalhouse,gourmetmarket,ihavecpu,jib,lotuss,makro,ofm,powerbuy,supersports,thaiwatsadu,tops,villamarket,watsons}/*.mjs; do node --check "$file" || exit 1; done`

Expected: exit status zero with no parse errors.

- [ ] **Step 4: Run existing project tests**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`

Expected: existing tests and new shopping tests pass.

### Task 10: Bootstrap Hetzner and transfer exact application data

**Files:**
- Deploy: `shopping_app/**` to `/home/anu/shopping/app/shopping_app/**`
- Deploy: 21 entrypoints and `out/craft/_shared/**` to `/home/anu/shopping/app/out/**`
- Transfer: `out/craft/*-products/plan.md` to `/home/anu/shopping/app/plans/**`
- Transfer: `out/craft/*-products/out/**` to `/home/anu/shopping/imports/initial/**`

- [ ] **Step 1: Create a local initial-data manifest**

Run: `find out/craft -path '*-products/out/*' -type f ! -name .DS_Store -print0 | sort -z | xargs -0 shasum -a 256 > /tmp/shopping-initial.sha256`

Expected: 295 manifest lines before deployment after excluding `.DS_Store`.

- [ ] **Step 2: Copy application and crawler files without unrelated worktree files**

Run exact-path rsync commands for `shopping_app`, the 21 entrypoint directories, `out/craft/_shared`, plans, and initial output directories. Use `--rsync-path='sudo rsync'` and do not use broad repository sync.

Expected: `/home/anu/shopping/app` and `/home/anu/shopping/imports/initial` contain only scoped deployment files.

- [ ] **Step 3: Execute the idempotent remote bootstrap**

Run: `ssh hetzner-anu 'sudo bash /home/anu/shopping/app/shopping_app/deploy/bootstrap-remote.sh'`

Expected: PostgreSQL database `shopping` exists, migrations and seeds pass, Bun and Python dependencies resolve, and systemd units load but the timer remains disabled.

- [ ] **Step 4: Verify remote application ownership and database isolation**

Run remote checks for `stat /home/anu/shopping/app`, writable runtime directories, `ss -lntp`, role attributes, database ownership, and grants.

Expected: application files are root-owned, runtime paths are shopping-owned, PostgreSQL still listens only on localhost, and the runtime role has no role or database creation privileges.

### Task 11: Verify transfer and ingest historical artifacts

**Files:**
- Verify: `/home/anu/shopping/imports/initial/**`
- Populate: PostgreSQL `shopping` tables

- [ ] **Step 1: Generate the remote checksum manifest**

Run: `ssh hetzner-anu 'sudo find /home/anu/shopping/imports/initial -type f ! -name .DS_Store -print0 | sudo sort -z | sudo xargs -0 sha256sum'`

Expected: 295 paths and no missing files.

- [ ] **Step 2: Compare normalized local and remote checksums**

Rewrite only the known local and remote prefixes before comparison.

Expected: zero checksum differences and total byte count `85991840`.

- [ ] **Step 3: Import each marketplace directory**

Run: `ssh hetzner-anu 'for site in advice allonline b2s bigc bnbhome boots central dohome globalhouse gourmetmarket ihavecpu jib lotuss makro ofm powerbuy supersports thaiwatsadu tops villamarket watsons; do sudo -u shopping /home/anu/shopping/app/.venv/bin/python -m shopping_app.cli ingest --site "$site" --path "/home/anu/shopping/imports/initial/$site" --mode initial; done'`

Expected: all 295 JSON files import or produce a path-specific JSON error; no file disappears.

- [ ] **Step 4: Repeat import and reconcile idempotency**

Run the same import loop, then query document, blob, projection, revision, and error counts.

Expected: the second pass does not increase document references or unchanged revisions.

### Task 12: Smoke-test every crawler on Hetzner

**Files:**
- Execute: `/home/anu/shopping/app/out/<site>/<site>.mjs`
- Populate: `/home/anu/shopping/data/<site>/<run-id>` and PostgreSQL

- [ ] **Step 1: Run Bun syntax and help checks for all sites**

Run each entrypoint with `bun --check` or Bun's parse command, then `bun run <entrypoint> --help`.

Expected: 21 syntax successes and 21 help exit statuses of zero.

- [ ] **Step 2: Run bounded smoke crawl and import for each site**

Run: `sudo systemctl start shopping-crawl@<site>.service` with `SHOPPING_MODE=smoke` through the manual CLI when template environment override is unavailable.

Expected: each currently available marketplace writes at least one product, finalizes a run, and creates a product projection. A source outage records a specific failed run and remains unscheduled until repaired.

- [ ] **Step 3: Verify resume behavior**

Repeat a smoke crawl into its retained output through `--resume` and inspect summary counters or skipped-file logs.

Expected: completed product files are skipped and no duplicate unchanged revision appears.

- [ ] **Step 4: Repair site-specific failures with a failing regression test**

For each actual failure, add one focused test that reproduces the incorrect parser, stop condition, or endpoint response shape. Run it red, implement the smallest fix, run it green, deploy the changed entrypoint, and repeat that site's smoke gate.

Expected: all 21 smoke gates pass or the final report names an external source outage with HTTP evidence.

### Task 13: Enable full crawls and prove backfill exports

**Files:**
- Enable: `shopping-scheduler.timer`
- Create: `/home/anu/shopping/exports/**`

- [ ] **Step 1: Queue all enabled marketplaces**

Run an SQL update that sets `next_crawl_at = now()` for the 21 seeded sources after their smoke gates pass.

Expected: the scheduler health command reports 21 due sources before the first run starts.

- [ ] **Step 2: Enable and start the timer**

Run: `ssh hetzner-anu 'sudo systemctl enable --now shopping-scheduler.timer'`

Expected: the timer is active, the scheduler starts one full run, and a second scheduler invocation exits without overlap.

- [ ] **Step 3: Create and verify a baseline export**

Run: `ssh hetzner-anu 'sudo -u shopping bash -lc "cd /home/anu/shopping/app && .venv/bin/python -m shopping_app.cli export-baseline"'`

Expected: a custom-format dump, manifest, and SHA-256 exist under `/home/anu/shopping/exports/baseline`.

- [ ] **Step 4: Restore baseline into a temporary database**

Create `shopping_restore_test`, restore the dump, compare schema version and table counts, then remove only the temporary database.

Expected: all table counts match the source database.

- [ ] **Step 5: Create and repeat an incremental export**

Run: `ssh hetzner-anu 'sudo -u shopping bash -lc "cd /home/anu/shopping/app && .venv/bin/python -m shopping_app.cli export-incremental"'` twice.

Expected: the first bundle contains records above the prior watermark; the second reports no new rows or emits an empty verified bundle without changing the completed watermark.

- [ ] **Step 6: Apply the incremental bundle twice to the temporary database**

Run the `import-bundle` command against `shopping_restore_test`, compare table counts, then run the same command again.

Expected: the first import reconciles source counts and the second reports `already_imported` without changing any table count.

- [ ] **Step 7: Run final live health checks**

Run systemd status, timer listing, process inspection, journal tail, database health, due-source, active-run, last-success, last-failure, disk, manifest, and export checksum checks.

Expected: PostgreSQL and the scheduler timer are active, one or zero crawler processes exist, no duplicate marketplace process exists, database queries succeed, more than 10 GiB remains free, and export checksums verify.

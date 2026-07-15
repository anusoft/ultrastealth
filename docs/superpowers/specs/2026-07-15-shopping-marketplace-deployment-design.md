# Deploy marketplace crawlers to Hetzner

This design deploys 21 retail marketplace crawlers to `hetzner-anu:/shopping`. Hetzner becomes the primary crawl store, while lossless files and versioned PostgreSQL exports support later on-premises backfill.

**Content type:** Conceptual design specification

## Document plan

This specification gives an implementation agent the approved scope and acceptance criteria:

- **Goal:** Deploy, migrate, crawl, ingest, schedule, export, and verify marketplace data on Hetzner
- **Audience:** Engineers operating the crawler host and the future on-premises PostgreSQL database
- **Content plan:** Scope, alternatives, architecture, schema, crawl flow, backfill flow, security, failure handling, testing, and rollout
- **Open questions:** None block implementation; schedule intervals remain editable seed data

## Goal and acceptance criteria

The deployment must retain every marketplace JavaScript Object Notation (JSON) field, expose common product fields for queries, and preserve enough metadata to reproduce each observation. It must also continue an interrupted crawl without replacing a previously successful run.

The work is complete when all these conditions hold:

1. `/shopping` contains the application, migrations, raw data, logs, exports, and deployment manifests.
2. PostgreSQL 17 contains a new `shopping` database with versioned migrations and seeds for all 21 marketplaces.
3. The initial 295 marketplace artifact files, totaling 85,991,840 bytes, exist on Hetzner with matching Secure Hash Algorithm 256-bit (SHA-256) digests and database records.
4. Every crawler passes syntax, help, smoke, persistence, and resume checks on Hetzner.
5. A systemd scheduler can queue and run full catalog crawls without overlapping the same marketplace.
6. A failed or interrupted crawl retains its partial files and checkpoint for the next run.
7. A baseline database dump and incremental export bundle can initialize and update an on-premises PostgreSQL database.
8. PostgreSQL remains bound to localhost, and the crawler runs without a reusable database password.

## Scope

The deployment covers these 21 marketplaces:

- advice
- allonline
- b2s
- bigc
- bnbhome
- boots
- central
- dohome
- globalhouse
- gourmetmarket
- ihavecpu
- jib
- lotuss
- makro
- ofm
- powerbuy
- supersports
- thaiwatsadu
- tops
- villamarket
- watsons

The initial transfer includes each marketplace plan, crawler entrypoint, shared runtime file, and existing crawl artifact under `out/craft/<site>-products/out`. It excludes electronic government procurement (EGP) artifacts, benchmark outputs, `.DS_Store` files, and unrelated working-tree changes.

The existing crawlers store product data and image URLs. This project does not download product image binaries because the current crawler contract does not produce them. Lossless data means every JSON object and field returned by the crawler, including raw payloads, category pages, reviews, summaries, and run metadata.

## Approaches considered

The selected hybrid design keeps immutable files and indexes their contents in PostgreSQL. This preserves crawler resume behavior and gives operators a queryable catalog.

### Hybrid files and PostgreSQL

Each crawl writes original files before an importer records them in PostgreSQL. A content-addressed blob table prevents duplicate JSON payloads, while current and revision tables support product queries and history.

This design is selected because it preserves source evidence, limits duplicate storage, and supports disconnected on-premises backfill.

### PostgreSQL-only writes

Each crawler could write directly to PostgreSQL. This would require invasive changes across seven standalone crawlers and the shared engine, and an interrupted database transaction would not provide the current file-based resume behavior.

### Files-only storage

The crawlers could continue writing JSON without ingestion. This would preserve source data but make cross-marketplace queries, history, deduplication, and incremental on-premises backfill harder to operate.

## Host architecture

The deployment uses the existing Ubuntu 24.04 host and PostgreSQL 17 cluster. It adds Bun, `scrapling-js`, application dependencies, a restricted Unix service account, and systemd units.

Database changes use ordered Structured Query Language (SQL) migrations. The target layout is:

```text
/shopping/
  app/                 deployed source and Bun dependencies
  app/out/             21 entrypoints and shared crawler runtime
  app/bin/             crawl, ingest, schedule, export, and health commands
  app/db/migrations/   ordered SQL migrations
  app/db/seeds/        idempotent marketplace seed data
  app/systemd/         source unit files
  data/                finalized lossless crawl runs
  partial/             resumable interrupted crawl directories
  imports/initial/     checksummed initial marketplace artifacts
  exports/             baseline and incremental on-premises bundles
  logs/                command-level logs not already in the journal
  state/               scheduler locks and generated manifests
```

The `shopping` Unix account owns runtime directories and has no interactive shell. Root owns deployed source and systemd unit files. Runtime code receives database access through PostgreSQL peer authentication on the local Unix socket.

## Application components

Each component has one operational responsibility and a stable command interface.

- **Marketplace manifest:** Defines the 21 slugs, entrypoints, supported full-crawl flags, smoke flags, output conventions, intervals, and enabled state
- **Crawl runner:** Creates a run record, acquires a marketplace lock, resumes a partial directory, executes Bun, finalizes successful output atomically, and records failure details
- **Importer:** Classifies every JSON file, validates JSON, stores its content by SHA-256, creates the crawl-document reference, and updates product projections in one transaction per file
- **Scheduler:** Selects one due marketplace, creates a queued run, executes it, and applies success or failure scheduling rules
- **Exporter:** Creates baseline dumps or incremental bundles with manifests, migration checksums, watermarks, record counts, and file checksums
- **Health command:** Reports database connectivity, disk space, scheduler state, active locks, last success, last failure, due marketplaces, and export watermarks

The manifest adapts crawler differences without hiding them. A full crawl always uses explicit unlimited flags. Watsons must accept zero as unlimited for categories, pages, products, review pages, and reviews instead of retaining smoke defaults.

## Filesystem lifecycle

The crawl runner uses an atomic directory lifecycle so operators can distinguish complete data from resumable work.

1. Create or reopen `/shopping/partial/<site>/<run-id>`.
2. Run the crawler with `--resume` and explicit full-catalog flags.
3. Retain the partial directory when the process exits unsuccessfully.
4. Write a final run manifest after the crawler succeeds.
5. Rename the directory to `/shopping/data/<site>/<run-id>` on the same filesystem.
6. Import every JSON file and mark the run imported only after all files succeed.

The runner never deletes a successful run. A separate retention command may be added later after storage requirements are measured and approved.

## PostgreSQL schema

The schema separates immutable source evidence from query projections. Raw documents retain fields that do not fit the common product model.

### Migration and source metadata

`schema_migrations` records the migration version, SHA-256, and application time. `marketplaces` stores the 21 seeded sources, their crawl configuration, interval, priority, enabled state, and next due time.

### Crawl execution

`crawl_runs` stores a universally unique identifier (UUID), marketplace, mode, state, timestamps, host, command arguments, output path, checkpoint, counters, and structured error. Valid states are `queued`, `running`, `succeeded`, `failed`, and `imported`.

`crawl_errors` stores item-level parse or request failures without truncating the parent run error. Each row includes the stage, source key, relative path, retryability, and JSON detail.

### Lossless documents

`document_blobs` stores one JSONB payload for each unique SHA-256. It also records the uncompressed byte count and first-seen time.

`crawl_documents` links a blob to a run, marketplace, document kind, relative path, source key, and capture time. The unique run and relative-path constraint makes repeated imports idempotent, while multiple runs may reference the same blob.

Document kinds include `product`, `category`, `category_page`, `review_page`, `summary`, `metadata`, `index`, and `other`. The importer uses path rules and payload shape, and unknown shapes remain available as `other`.

### Product query model

`products_current` uses `(marketplace_id, source_product_id)` as its primary key. It stores the latest document reference, first and last observation times, URL, stock keeping unit, title, brand, prices, currency, availability, category path, image URLs, rating, review count, and a JSONB projection for site-specific normalized fields.

`product_revisions` stores a new row only when the canonical product digest changes. The canonical form excludes volatile capture fields such as `scrapedAt`, run identifiers, and request timestamps, while the raw blob digest still covers the exact JSON document. Identical observations remain visible through `crawl_documents` without duplicating the payload or revision.

The importer resolves source product identifiers through marketplace adapters. It checks known fields such as `id`, `sku`, `code`, and nested `identity`, then rejects a product projection when no stable identifier exists. The original document remains stored, and `crawl_errors` records the projection failure.

### Export tracking

`export_batches` records a bundle UUID, type, lower and upper document watermarks, schema version, path, SHA-256, counts, creation time, and completion state. `imported_batches` lets an on-premises target reject or skip a bundle it has already applied.

The schema indexes marketplace and source keys, run states, due times, observation times, document kinds, revision watermarks, product stock keeping units, and product URLs. A PostgreSQL generalized inverted index (GIN) covers the product projection, while the large immutable blob payload remains unindexed until a measured query requires it.

## Crawl scheduling and concurrency

The scheduler runs from a systemd timer every 15 minutes and starts at most one due full crawl. A global scheduler lock prevents concurrent scheduler processes, and a marketplace lock prevents duplicate crawls for one site.

Each marketplace starts with a seven-day interval in seed data. Operators can change intervals without editing unit files. Success advances `next_crawl_at` by the configured interval, while failure applies bounded backoff and retains the partial directory.

The initial rollout queues all marketplaces but processes them sequentially. This controls host resource use and reduces pressure on retailer endpoints. Existing per-request delays and retry behavior remain enabled.

## Initial transfer and ingestion

The initial migration copies 295 files and verifies every SHA-256 before ingestion. The transfer also creates a source manifest that records the local path, remote path, byte count, and digest.

The importer treats each existing artifact directory as an imported historical run. It derives the marketplace from the path, records the original relative path, and preserves any existing run summary or metadata.

The transfer does not overwrite a remote file with a different checksum. A mismatch stops the migration and reports both paths and digests.

## On-premises PostgreSQL backfill

The export path supports an offline on-premises database without granting it network access to Hetzner.

A baseline export uses PostgreSQL custom format and includes schema, seeds, immutable documents, projections, revisions, and export metadata. The export command writes a manifest and SHA-256 beside the dump.

Incremental exports contain records above the last completed document and revision watermarks. Each compressed bundle includes:

- migration files and their checksums
- an export manifest with source and target schema versions
- document references and any previously unseen blobs
- changed product revisions and current projections
- crawl runs and errors within the watermark range
- row counts and checksums for each data stream

The on-premises import command applies pending migrations, verifies checksums, loads rows with conflict-safe keys, updates current projections, and records the bundle UUID in `imported_batches`. A repeated import exits successfully without duplicating data.

## Security boundaries

The deployment keeps database and service access local to the host. It does not open a new TCP port.

- PostgreSQL continues listening on localhost
- The Unix `shopping` account maps to a restricted PostgreSQL runtime role through peer authentication
- A non-login database owner owns schema objects
- The runtime role can read and write application tables but cannot create roles, databases, or extensions
- Root applies migrations and installs systemd units
- `/shopping/app` is root-owned and not writable by the runtime account
- `/shopping/data`, `/shopping/partial`, `/shopping/logs`, and `/shopping/state` are writable only by the runtime account

Crawler endpoints and public storefront tokens already embedded in the generated source remain deployable artifacts. The deployment does not copy shell history, browser profiles, local environment files, SSH keys, or unrelated repository files.

## Failure handling

Each stage records enough state for recovery without guessing what completed.

- **Crawler failure:** Mark the run failed, retain its partial directory, store stderr details, and schedule bounded retry
- **Invalid JSON:** Store the path and error, leave the original file untouched, and keep the run short of imported state
- **Missing product identifier:** Store the lossless document, record a projection error, and continue importing other files
- **Database interruption:** Roll back the current file transaction and repeat it safely through unique constraints
- **Disk pressure:** Refuse to start a crawl when free space is below 10 GB and report the health failure
- **Concurrent execution:** Exit without starting when the global or marketplace lock already exists
- **Export interruption:** Leave the batch incomplete and exclude it from the next watermark until a verified bundle succeeds
- **Site drift:** Fail on empty or suspiciously short output when the source reports a higher total; retain evidence for crawler repair

## Testing and verification

Verification combines local tests, remote smoke runs, data checksums, and database assertions. A crawler only becomes schedulable after its smoke gate passes.

### Automated tests

Tests cover manifest validation, full and smoke argument generation, path classification, source identifier extraction, SHA-256 deduplication, idempotent import, changed-product revisions, failed JSON handling, export watermark selection, and repeated bundle import.

SQL tests apply all migrations to a fresh database, run seeds twice, validate grants, and confirm every foreign key and unique constraint. The migration test also compares recorded migration checksums with source files.

### Remote crawler checks

Each of the 21 entrypoints must pass these checks on Hetzner:

1. Bun parses the file.
2. `--help` exits with status zero.
3. A bounded smoke crawl writes at least one valid product when the source currently has products.
4. The importer stores every smoke JSON file and creates a current product projection.
5. A repeated smoke import creates no duplicate document references or revisions.
6. A second crawl with `--resume` reports skipped completed files.
7. Full arguments remove category, page, product, and review limits.

Where an endpoint exposes a total count, the full crawler must reconcile written unique products with that count. Sites without a reliable total must record discovered categories, pages, unique products, and stop reasons in the run summary.

### Transfer checks

The initial transfer passes only when local and remote manifests contain the same 295 relative paths, byte counts, and SHA-256 values. Database queries must then reconcile document counts with the imported manifest and report projection exceptions separately.

### Operational checks

The final rollout verifies the systemd timer, one queued full crawl, scheduler locking, journal output, database health, disk guard, baseline export, incremental export, and an idempotent restore into a temporary PostgreSQL database.

## Rollout sequence

The rollout orders changes so each stage can be verified before the next stage depends on it.

1. Build and test deployment artifacts locally.
2. Create the remote filesystem and restricted accounts.
3. Install Bun and application dependencies.
4. Create the PostgreSQL roles and `shopping` database.
5. Apply migrations and seeds.
6. Deploy crawlers, shared runtime, commands, and systemd source files.
7. Copy and verify existing marketplace artifacts.
8. Import the initial artifacts and reconcile counts.
9. Run all 21 bounded smoke crawls and persistence checks.
10. Install and enable scheduler units.
11. Queue the first sequential full-catalog cycle.
12. Create and restore-test baseline and incremental exports.
13. Report file counts, database counts, run states, export checksums, and remaining site-specific failures.

The rollout never changes or restarts the existing EGP application. PostgreSQL role and database creation share the cluster but do not modify the `egp` database.

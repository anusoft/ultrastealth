CREATE SCHEMA IF NOT EXISTS shopping AUTHORIZATION shopping_owner;

CREATE TABLE shopping.schema_migrations (
    version text PRIMARY KEY,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shopping.marketplaces (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+$'),
    display_name text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    crawl_interval_seconds integer NOT NULL CHECK (crawl_interval_seconds > 0),
    priority integer NOT NULL DEFAULT 100,
    enabled boolean NOT NULL DEFAULT true,
    next_crawl_at timestamptz NOT NULL DEFAULT now(),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shopping.crawl_runs (
    id uuid PRIMARY KEY,
    marketplace_id bigint NOT NULL REFERENCES shopping.marketplaces(id) ON DELETE RESTRICT,
    mode text NOT NULL CHECK (mode IN ('initial', 'smoke', 'full')),
    state text NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'imported')),
    host text NOT NULL,
    command_args jsonb NOT NULL DEFAULT '[]'::jsonb,
    output_path text,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    counters jsonb NOT NULL DEFAULT '{}'::jsonb,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    imported_at timestamptz
);

CREATE TABLE shopping.crawl_errors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES shopping.crawl_runs(id) ON DELETE CASCADE,
    marketplace_id bigint NOT NULL REFERENCES shopping.marketplaces(id) ON DELETE RESTRICT,
    stage text NOT NULL,
    source_key text,
    relative_path text,
    retryable boolean NOT NULL DEFAULT false,
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shopping.document_blobs (
    content_sha256 text PRIMARY KEY CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    first_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shopping.crawl_documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES shopping.crawl_runs(id) ON DELETE CASCADE,
    marketplace_id bigint NOT NULL REFERENCES shopping.marketplaces(id) ON DELETE RESTRICT,
    document_kind text NOT NULL,
    relative_path text NOT NULL,
    source_key text,
    content_sha256 text NOT NULL REFERENCES shopping.document_blobs(content_sha256) ON DELETE RESTRICT,
    captured_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, relative_path)
);

CREATE TABLE shopping.products_current (
    marketplace_id bigint NOT NULL REFERENCES shopping.marketplaces(id) ON DELETE RESTRICT,
    source_product_id text NOT NULL,
    latest_document_id bigint NOT NULL REFERENCES shopping.crawl_documents(id) ON DELETE RESTRICT,
    latest_run_id uuid NOT NULL REFERENCES shopping.crawl_runs(id) ON DELETE RESTRICT,
    canonical_sha256 text NOT NULL CHECK (canonical_sha256 ~ '^[0-9a-f]{64}$'),
    sku text,
    title text,
    brand text,
    source_url text,
    current_price numeric,
    regular_price numeric,
    currency text,
    availability jsonb,
    category_path jsonb NOT NULL DEFAULT '[]'::jsonb,
    image_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
    rating numeric,
    review_count bigint,
    projection jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (marketplace_id, source_product_id)
);

CREATE TABLE shopping.product_revisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    marketplace_id bigint NOT NULL REFERENCES shopping.marketplaces(id) ON DELETE RESTRICT,
    source_product_id text NOT NULL,
    run_id uuid NOT NULL REFERENCES shopping.crawl_runs(id) ON DELETE RESTRICT,
    document_id bigint NOT NULL REFERENCES shopping.crawl_documents(id) ON DELETE RESTRICT,
    canonical_sha256 text NOT NULL CHECK (canonical_sha256 ~ '^[0-9a-f]{64}$'),
    observed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (marketplace_id, source_product_id, canonical_sha256),
    FOREIGN KEY (marketplace_id, source_product_id)
        REFERENCES shopping.products_current(marketplace_id, source_product_id)
        ON DELETE RESTRICT
);

CREATE TABLE shopping.export_batches (
    id uuid PRIMARY KEY,
    batch_type text NOT NULL CHECK (batch_type IN ('baseline', 'incremental')),
    lower_document_id bigint NOT NULL DEFAULT 0,
    upper_document_id bigint NOT NULL DEFAULT 0,
    lower_revision_id bigint NOT NULL DEFAULT 0,
    upper_revision_id bigint NOT NULL DEFAULT 0,
    schema_version text NOT NULL,
    output_path text NOT NULL,
    content_sha256 text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL CHECK (state IN ('running', 'complete', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE shopping.imported_batches (
    id uuid PRIMARY KEY,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    source_schema_version text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX marketplaces_due_idx
    ON shopping.marketplaces (enabled, next_crawl_at, priority);
CREATE INDEX crawl_runs_marketplace_state_idx
    ON shopping.crawl_runs (marketplace_id, state, created_at DESC);
CREATE INDEX crawl_documents_marketplace_kind_idx
    ON shopping.crawl_documents (marketplace_id, document_kind, id);
CREATE INDEX crawl_documents_source_key_idx
    ON shopping.crawl_documents (marketplace_id, source_key)
    WHERE source_key IS NOT NULL;
CREATE INDEX products_current_sku_idx
    ON shopping.products_current (marketplace_id, sku)
    WHERE sku IS NOT NULL;
CREATE INDEX products_current_url_idx
    ON shopping.products_current (source_url)
    WHERE source_url IS NOT NULL;
CREATE INDEX products_current_projection_gin_idx
    ON shopping.products_current USING gin (projection);
CREATE INDEX product_revisions_watermark_idx
    ON shopping.product_revisions (id, observed_at);

GRANT USAGE ON SCHEMA shopping TO shopping;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shopping TO shopping;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shopping TO shopping;
ALTER DEFAULT PRIVILEGES FOR ROLE shopping_owner IN SCHEMA shopping
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shopping;
ALTER DEFAULT PRIVILEGES FOR ROLE shopping_owner IN SCHEMA shopping
    GRANT USAGE, SELECT ON SEQUENCES TO shopping;

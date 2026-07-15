DELETE FROM shopping.crawl_errors duplicate
USING shopping.crawl_errors retained
WHERE duplicate.id > retained.id
  AND duplicate.run_id = retained.run_id
  AND duplicate.stage = retained.stage
  AND duplicate.relative_path IS NOT DISTINCT FROM retained.relative_path
  AND duplicate.source_key IS NOT DISTINCT FROM retained.source_key;

CREATE UNIQUE INDEX crawl_errors_identity_idx
    ON shopping.crawl_errors (run_id, stage, relative_path, source_key)
    NULLS NOT DISTINCT;

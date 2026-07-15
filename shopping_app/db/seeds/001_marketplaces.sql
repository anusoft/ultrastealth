INSERT INTO shopping.marketplaces (
    slug,
    display_name,
    crawl_interval_seconds,
    priority,
    enabled
)
VALUES
    ('advice', 'Advice', 604800, 1, true),
    ('allonline', 'AllOnline', 604800, 2, true),
    ('b2s', 'B2S', 604800, 3, true),
    ('bigc', 'Big C', 604800, 4, true),
    ('bnbhome', 'BnB Home', 604800, 5, true),
    ('boots', 'Boots', 604800, 6, true),
    ('central', 'Central', 604800, 7, true),
    ('dohome', 'Dohome', 604800, 8, true),
    ('globalhouse', 'Global House', 604800, 9, true),
    ('gourmetmarket', 'Gourmet Market', 604800, 10, true),
    ('ihavecpu', 'iHaveCPU', 604800, 11, true),
    ('jib', 'JIB', 604800, 12, true),
    ('lotuss', 'Lotus''s', 604800, 13, true),
    ('makro', 'Makro', 604800, 14, true),
    ('ofm', 'OfficeMate', 604800, 15, true),
    ('powerbuy', 'Power Buy', 604800, 16, true),
    ('supersports', 'Supersports', 604800, 17, true),
    ('thaiwatsadu', 'Thai Watsadu', 604800, 18, true),
    ('tops', 'Tops', 604800, 19, true),
    ('villamarket', 'Villa Market', 604800, 20, true),
    ('watsons', 'Watsons', 604800, 21, true)
ON CONFLICT (slug) DO UPDATE
SET display_name = EXCLUDED.display_name,
    updated_at = now();

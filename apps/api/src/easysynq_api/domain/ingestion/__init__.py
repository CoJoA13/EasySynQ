"""Pure ingestion domain logic (slice S-ing-1, doc 09): the SourceProvider seam + FileMeta, the §4.2
filters/quarantine classifier, and the §4.3 inventory-summary reducer. No IO, no libmagic, no DB —
everything here is unit-testable with plain values."""

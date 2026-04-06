# Changelog

All notable changes to Bias are documented here.

---

## [0.2.0] — 2026-03-29

### Added
- **Dev dashboard** — `python -m pipeline.dashboard --open` generates a self-contained HTML file showing all 25 journalists, distribution bars, average score, and a full article list (title · date · bucket · score) per journalist. Filter by scored/unscored. Click any card to expand.
- **archive.is paywall fallback** — NZ Herald articles returning < 200 chars of text (paywalled) are automatically retried via `archive.is/newest/{url}`. Significantly increases yield from NZ Herald author pages.
- **`--backfill` flag** — raises article cap to 200 per journalist for the initial historical data collection run. Use once per journalist to build a proper baseline.
- **`--cap N` flag** — set a custom article cap per journalist per run (default: 20).
- **DEMO DATA badge** — hover cards show a small amber "DEMO DATA" badge when the journalist's data comes from placeholder scores rather than real pipeline output. Disappears automatically once real data is loaded.
- **RNZ adapter** added to the pipeline orchestrator (was built but not wired up).
- **python-dotenv** — `.env` file loaded automatically via dotenv on every pipeline run. No need to `export` the key manually.

### Fixed
- NZ Herald Playwright timeout — changed `wait_until` from `networkidle` to `domcontentloaded`. Author pages now load reliably in < 5s (was timing out after 20s).
- Duplicate connections — `connections` table now has a `UNIQUE` constraint on `(journalist_id, type, target_name, source_url)`. `INSERT OR IGNORE` prevents re-insertion on every run. 78 stale duplicates removed from existing DB.
- `data.json` wiped on failed runs — exporter now skips writing if 0 journalists scored and the output file already exists.
- Silent API failures — scorer now validates `ANTHROPIC_API_KEY` on startup and logs a clear error instead of swallowing 401s silently.
- Content script bundle size — rewritten from React to vanilla DOM. Bundle dropped from 3.5 MB to 25 KB.
- `chrome.storage.local` not available in content scripts — `data.ts` now fetches directly (GitHub URL first, bundled fallback second).

### Changed
- Extension entrypoints moved from `extension/src/entrypoints/` to `extension/entrypoints/` (WXT requirement).
- Stuff adapter now tries an author search page before falling back to general politics RSS.

---

## [0.1.0] — 2026-03-28

### Added
- Initial project: WXT Chrome extension + Python pipeline
- Content script with byline detection (JSON-LD → meta → CSS selector fallback)
- Hover card with distribution bars, connections, confidence badge, animation
- Shadow DOM isolation — card styles never bleed into host site
- NZ Herald adapter (Playwright) and Stuff adapter (Trafilatura)
- RNZ adapter (Trafilatura + RSS)
- `scorer.py` — Claude API scoring on −1.0 to +1.0 scale across 5 dimensions
- `aggregator.py` — bucketing into Left / Centre-Left / Centre / Centre-Right / Right
- `exporter.py` — SQLite → `data.json` for extension consumption
- `data/journalists.csv` — 25 NZ political journalists seeded
- `data/connections.csv` — 13 verified connections with source URLs
- GitHub Actions `score.yml` — daily pipeline cron
- TypeScript tests for extension lib (`detect`, `match`, `data`)
- Python tests for pipeline (`aggregator`, `exporter`, `scorer`, `scraper`)

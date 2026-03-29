# Byline Card — NZ Journalist Transparency

A Chrome extension that shows the political lean and connections of the journalist writing the article you're reading. Hover any byline on a NZ news site — a card appears with AI-scored article distribution, documented connections, and sourced facts. NZ-only. Open source.

## What it shows

- **Coverage distribution** — percentage of articles scoring Left / Centre-Left / Centre / Centre-Right / Right (AI analysis of framing, source selection, language, topic emphasis)
- **Documented connections** — family ties to politicians, marriages to other journalists, employer relationships — every connection links to its source
- **Confidence level** — based on article count (low < 25, medium < 100, high 100+)
- **DEMO DATA badge** — visible in dev when using placeholder data, disappears when real pipeline data is loaded

## Supported sites

NZ Herald · Stuff · RNZ · 1News · Newsroom · The Spinoff · interest.co.nz

## Install (developer mode)

```bash
git clone https://github.com/ferguswatts/byline-card.git
cd byline-card/extension
npm install
npx wxt build
```

Then: Chrome → `chrome://extensions` → Enable Developer mode → Load unpacked → select `extension/.output/chrome-mv3`

## Pipeline

The scoring pipeline scrapes NZ Herald, Stuff, and RNZ author pages, extracts article text (with `archive.is` fallback for paywalled articles), scores each via Claude API, and exports `data.json` for the extension.

### Run locally

```bash
cd byline-card
pip install -r pipeline/requirements.txt
playwright install chromium
echo 'ANTHROPIC_API_KEY=your_key' > .env

# Normal run — 20 articles max per journalist
python -m pipeline.run

# First-time backfill — 200 articles max (gets full history)
python -m pipeline.run --backfill

# Limit to N journalists
python -m pipeline.run --journalists 5

# Custom article cap
python -m pipeline.run --cap 50
```

### Dev dashboard

Visualise every scored article per journalist directly from the SQLite database:

```bash
python -m pipeline.dashboard --open
```

Shows distribution bars, average score, and a full article list (title · date · bucket · score) for each journalist. Filter by scored/unscored. Click any journalist to expand.

## Architecture

```
Chrome Extension (WXT + TypeScript)
  └── content script detects bylines, renders hover card (Shadow DOM)
  └── loads data.json — tries GitHub raw URL, falls back to bundled copy

data.json ← pipeline exports after each run

Python Pipeline (SQLite)
  ├── Per-site adapters: NZ Herald (Playwright), Stuff, RNZ (Trafilatura)
  ├── archive.is fallback for paywalled NZ Herald articles
  ├── Claude API scores each article (-1.0 to +1.0)
  └── Aggregator buckets scores → distribution percentages
```

Two moving parts. No API server. No database server. No hosting costs.

## Phase 1 journalists (live data)

| Journalist | Outlet | Articles scored |
|---|---|---|
| Audrey Young | NZ Herald | 21+ |
| Claire Trevett | NZ Herald | 36+ |
| Derek Cheng | NZ Herald | 35+ |
| Jason Walls | NZ Herald | 34+ |
| Thomas Coughlan | NZ Herald | 36+ |
| Michael Neilson | NZ Herald | 35+ |

25 journalists seeded in `data/journalists.csv`. Adapters for 1News, Newsroom, and Spinoff coming in Phase 2.

## Methodology

Articles are scored by Claude API on a −1.0 to +1.0 scale across five dimensions:

| Dimension | What it measures |
|---|---|
| Framing | How the topic is presented; who is protagonist/antagonist |
| Source selection | Which voices are quoted; are opposing views included |
| Language | Loaded or emotive language ("slammed", "radical", "controversial") |
| Topic emphasis | What aspects are highlighted vs downplayed |
| Omission | What relevant context or perspectives are missing |

Scores bucket into: **Left** (≤−0.6) · **Centre-Left** (−0.6 to −0.2) · **Centre** (−0.2 to +0.2) · **Centre-Right** (+0.2 to +0.6) · **Right** (≥+0.6)

The card shows the distribution across all scored articles — not a single score. This is deliberate: a journalist may write centrist news pieces and left-leaning opinion pieces. The distribution shows both.

Every connection on the card links to a public source. No unsourced claims.

## Contributing

- **Add a journalist:** Edit `data/journalists.csv`
- **Add a connection:** Edit `data/connections.csv` (source URL required)
- **Add a site adapter:** Create `pipeline/sites/newsite.py` implementing `SiteAdapter`
- **Fix a connection:** Open a PR with corrected source URL

All contributions reviewed before merging.

## License

MIT

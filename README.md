# sarkari-crawler

Finds new central and state government job notifications on official websites, extracts
them with a free LLM (GPT-OSS-120B on Groq), and publishes them to Supabase without a
reviewer. Runs on GitHub Actions every 30 minutes.

This repo is public. API keys and database credentials live only in GitHub Actions
secrets, never in code.

## How a notice gets published

```
sources ─crawl─▶ links ─keyword rules─▶ download ─text check─▶ LLM (2 calls) ─publish gate─▶ publish_draft() ─▶ Supabase
```

1. **Crawl** (`discover.py`, `fetch.py`): each due source's listing page is fetched politely
   (robots.txt, a delay per host, size cap) and its notice links are stored once each.
   A source's first crawl queues only its 5 newest notice-like links.
2. **Filter without AI** (`classify.py`): results, admit cards, answer keys, tenders,
   syllabi, interview schedules and similar are dropped by keyword rules, first on the
   link label and again on the downloaded text. LLM quota is spent only on files that read
   like a notification or corrigendum.
3. **Read** (`pdf.py`): the file is read in memory and never stored; only its official
   link, hash and size are kept, and the site links to the official URL. PyMuPDF text, Tesseract OCR (English + Hindi) for scans, HTML
   notices as text. Bilingual notices use their English half; Hindi-first state notices
   keep their Hindi pages. Pages are chosen by keyword within a token budget so every
   call fits Groq's free 8,000 tokens per minute.
4. **Extract** (`extractors/`): two calls (header, then posts) validated against
   `sarkari_core.schemas`. Low reasoning effort, a reply cap per step, retries on
   per-minute limits; a daily-limit 429 stops extraction and leaves the queue for the
   next run.
5. **Publish gate** (`sarkari_core/quality.py`): no reviewer, so rule checks decide:

   | Confidence | When | On the site |
   |---|---|---|
   | HIGH | every check passed | the full notice |
   | MEDIUM | warnings, or fields the model was unsure of | full notice; those fields marked "verify on the official notice" |
   | LOW | the posts step failed | dates and the official link only |
   | not published | no usable header, no last date, or last date before start | nothing (visible in the admin as "Not published") |

6. **Publish** (`store.py`): the crawler inserts a PENDING draft and calls the database
   function `publish_draft()` (defined in sarkari-web). It creates the recruitment and its
   posts, skips re-uploads of the same advertisement, applies corrigenda (new dates,
   cancellations) to the recruitment they amend, and closes expired ones daily.

## Packages

| Package | What it is | Install |
|---|---|---|
| `sarkari_core` | The contract with sarkari-web: draft schemas, enums (incl. states), rule checks, the publish gate, versioned prompts | `pip install sarkari-crawler` (base, only needs pydantic) |
| `sarkari_crawler` | Crawler, PDF/HTML reading, Groq/OpenRouter extractors, pipeline, eval and golden-set tools | `pip install "sarkari-crawler[crawler]"` |

sarkari-web pins `sarkari_core` by git tag. Any incompatible change to the schemas bumps
`SCHEMA_VERSION` in `sarkari_core/schemas.py`; `notice_key()` in `sarkari_core/quality.py`
must stay identical to `catalog_notice_key()` in the database (a sarkari-web test checks).

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Optional, for scanned PDFs: `brew install tesseract tesseract-lang` (macOS) or
`sudo apt-get install tesseract-ocr tesseract-ocr-hin`. Without it, pages with no text
layer are skipped with a warning.

Run against a database (local Postgres from sarkari-web's docker compose, or Supabase):

```bash
cp .env.example .env   # fill in SARKARI_DATABASE_URL and GROQ_API_KEY
set -a; source .env; set +a
.venv/bin/python -m sarkari_crawler.run crawl --no-extract --max-sources 5   # links only
.venv/bin/python -m sarkari_crawler.run crawl --max-documents 3              # extract and publish
.venv/bin/python -m sarkari_crawler.run report
```

## Free-tier budget

Groq's limits page lists 30 requests/min, 1,000 requests/day, 8,000 tokens/min and
200,000 tokens/day for `openai/gpt-oss-120b` (check your account's limits page; these
change). A notice costs roughly 10,000–15,000 tokens, so expect **about 13–20 notices a
day**. Central sources (priority 1) are extracted first, newest first; the rest wait for
the next day's quota. An OpenRouter free model can be added as a fallback in
`SARKARI_EXTRACTORS`; free OpenRouter models allow 50 requests a day.

## Sources

Sources live in the database (`ingest_source`), managed in the sarkari-web admin and
seeded with `python manage.py load_sources`. Per-source `parser_config`:

- `{"link_selector": "table#notices a"}`: CSS selector for the notices table on busy pages.
- `{"verify_tls": false}`: lenient TLS for government servers with broken certificate
  chains or legacy renegotiation (no certificate check; use only when strict TLS fails).

The daily report lists sources that keep failing and sources whose page yielded no
notice links (they need a `link_selector`, or a custom parser for JavaScript sites).

## Measuring accuracy

```bash
.venv/bin/python -m sarkari_crawler.eval --json-out report.json
```

The eval scores only fields that the hand-checked `expected.json` fills in. A value the model
returns where the expected field is `null` is reported as a hallucination. See
[tests/golden/README.md](tests/golden/README.md) for how to add cases. Prompts live in
`src/sarkari_core/prompts/<step>_<version>.txt`; never edit a released version in place:
add the next version, bump `PROMPT_VERSION`, and compare the two with the eval.

## GitHub Actions

| Workflow | When | Needs |
|---|---|---|
| `ci.yml` | Every push and pull request | nothing |
| `crawl.yml` | Every 30 minutes; manual | secrets `SARKARI_DATABASE_URL`, `GROQ_API_KEY`; optional `OPENROUTER_API_KEY`; variables `SARKARI_EXTRACTORS` (default `groq:openai/gpt-oss-120b`), `SARKARI_USER_AGENT` |
| `daily-report.yml` | 09:00 IST; manual | secret `SARKARI_DATABASE_URL`; optional `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `eval.yml` | On changes to prompts, schemas or the pipeline; weekly; manual | secrets `GROQ_API_KEY`, `OPENROUTER_API_KEY`; variables `SARKARI_EXTRACTORS`, optional `EVAL_MIN_ACCURACY` (default 0.85) |

GitHub pauses scheduled workflows in a public repo after 60 days without a commit;
re-enable `crawl.yml` from the Actions tab if it stops.

## Known limits

- JavaScript-only sites (SSC, RRB apply portal, MPSC, APPSC, RSSB) have no links in their
  HTML; they're inactive until a custom parser for their JSON APIs is written.
- UPSC returned HTTP 403 to the crawler; MPPSC's robots.txt disallows crawling (respected).
- Notices only in a regional language other than Hindi (Tamil, Kannada, Bengali…) are
  skipped by the keyword rules and the prompts; most such boards also publish English.
- GitHub's runners are outside India; a site that blocks foreign traffic needs a
  self-hosted runner in India.

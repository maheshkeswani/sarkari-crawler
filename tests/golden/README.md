# Golden set

Hand-checked notifications that `python -m sarkari_crawler.eval` scores extractors against.
Target: 30 cases covering the hard formats below. Only cases with an `expected.json` are scored.

## Adding a case

```bash
python -m sarkari_crawler.golden new ssc-cgl-2026 ~/Downloads/cgl-2026.pdf --extractor "$SARKARI_EXTRACTORS"
# open tests/golden/ssc-cgl-2026/expected.draft.json next to the PDF and fix every field
mv tests/golden/ssc-cgl-2026/expected.draft.json tests/golden/ssc-cgl-2026/expected.json
python -m sarkari_crawler.golden check   # schema + rule checks on every expected.json
```

Rules for `expected.json`:
- Only what the PDF states. Leave a field `null` when the notification doesn't give it,
  even if you know the answer; that's what the eval treats as "should be empty".
- `age_relaxations` stays `null` unless the notification lists its own relaxation years.
- One post per distinct post name; regional splits are summed (same rule as the prompt).

## Cases to collect

| # | Case id | Why it's hard |
|---|---|---|
| 1–3 | `ssc-cgl-*`, `ssc-chsl-*`, `ssc-mts-*` | 30+ posts, pay levels, post-wise age limits |
| 4–5 | `ssc-gd-*`, `ssc-cpo-*` | Gender-wise vacancies, physical standards |
| 6–8 | `rrb-ntpc-*`, `rrb-group-d-*`, `rrb-alp-*` | Zone-wise vacancy tables, CEN numbers |
| 9–11 | `ibps-po-*`, `ibps-clerk-*`, `ibps-rrb-*` | Category tables, state-wise vacancies |
| 12–13 | `sbi-po-*`, `sbi-clerk-*` | Bank fee structure, final-year rules |
| 14–16 | `upsc-cse-*`, `upsc-ese-*`, `upsc-epfo-*` | 50+ pages, long eligibility sections |
| 17–18 | `upsc-cds-*`, `upsc-nda-*` | Date-of-birth windows instead of ages, gender rules |
| 19–21 | defence/agnipath notices | Scanned PDFs (OCR path) |
| 22–24 | `*-corrigendum-*` | Date extensions, vacancy revisions |
| 25–27 | PSU notices (e.g. a CPSE recruitment) | Experience requirements, discipline tags |
| 28–30 | Hindi-first bilingual PDFs | English half comes second |

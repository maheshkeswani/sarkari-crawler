"""Run an extractor over the golden set and report field-level accuracy.

    python -m sarkari_crawler.eval --extractor groq:<model>
    python -m sarkari_crawler.eval --cases ssc-cgl-2026 --json-out report.json --min-accuracy 0.85

Exit codes: 0 = done (and above --min-accuracy if given), 1 = below threshold,
2 = nothing to evaluate or bad configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from sarkari_core.extractor import ExtractionError
from sarkari_core.rules import check_draft

from .extractors import get_extractor
from .golden import DEFAULT_GOLDEN_DIR, GoldenCase, load_cases
from .pdf import prepare_document
from .scoring import CaseScore, FieldSummary, score_case, summarise


def run_eval(
    extractor,
    cases: list[GoldenCase],
    delay_seconds: float = 0.0,
    save_outputs: Optional[Path] = None,
    log=print,
) -> list[CaseScore]:
    scores = []
    for index, case in enumerate(cases):
        if index and delay_seconds:
            time.sleep(delay_seconds)  # stay under free-tier rate limits
        started = time.monotonic()
        document = prepare_document(case.pdf_path.read_bytes())
        try:
            result = extractor.extract(document)
        except ExtractionError as exc:
            score = score_case(case.case_id, case.expected, None)
            score.errors.append(str(exc))
            scores.append(score)
            log(f"{case.case_id}: provider failed: {exc}")
            continue

        score = score_case(case.case_id, case.expected, result.draft)
        score.errors.extend(result.errors)
        scores.append(score)

        flags = check_draft(result.draft) if result.draft else []
        _, case_summary = summarise([score])
        log(
            f"{case.case_id}: {_pct(case_summary.accuracy)} fields correct, "
            f"posts {score.matched_posts}/{score.expected_posts} matched, "
            f"{len(flags)} rule flag(s), {len(result.errors)} error(s), {time.monotonic() - started:.1f}s"
        )
        if save_outputs:
            save_outputs.mkdir(parents=True, exist_ok=True)
            output = {
                "extractor": result.extractor,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "draft": result.draft.model_dump(mode="json") if result.draft else None,
                "errors": result.errors,
                "rule_flags": [f.as_dict() for f in flags],
                "raw_outputs": result.raw_outputs,
            }
            (save_outputs / f"{case.case_id}.json").write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return scores


def _pct(value: Optional[float]) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def format_report(per_field: dict[str, FieldSummary], overall: FieldSummary) -> str:
    lines = [f"{'field':32} {'accuracy':>9} {'correct':>9} {'halluc.':>8}"]
    for name in sorted(per_field):
        s = per_field[name]
        lines.append(f"{name:32} {_pct(s.accuracy):>9} {f'{s.correct}/{s.scored}':>9} {s.hallucinated:>8}")
    lines.append(f"{'OVERALL':32} {_pct(overall.accuracy):>9} {f'{overall.correct}/{overall.scored}':>9} {overall.hallucinated:>8}")
    return "\n".join(lines)


def report_json(extractor_name: str, scores: list[CaseScore], per_field: dict[str, FieldSummary], overall: FieldSummary) -> dict:
    return {
        "extractor": extractor_name,
        "overall": {"accuracy": overall.accuracy, "correct": overall.correct, "scored": overall.scored, "hallucinated": overall.hallucinated},
        "fields": {
            name: {"accuracy": s.accuracy, "correct": s.correct, "scored": s.scored, "hallucinated": s.hallucinated}
            for name, s in sorted(per_field.items())
        },
        "cases": [
            {
                "case_id": c.case_id,
                "post_recall": c.post_recall,
                "post_precision": c.post_precision,
                "errors": c.errors,
                "wrong_fields": [
                    {"field": f.field, "expected": str(f.expected), "actual": str(f.actual)}
                    for f in c.fields
                    if f.scored and not f.correct
                ],
            }
            for c in scores
        ],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sarkari_crawler.eval")
    parser.add_argument("--extractor", help="extractor spec; defaults to $SARKARI_EXTRACTORS")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--cases", help="comma-separated case ids (default: all verified cases)")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between cases, for rate limits")
    parser.add_argument("--json-out", type=Path, help="write a JSON report here")
    parser.add_argument("--save-outputs", type=Path, help="write each case's raw extraction here")
    parser.add_argument("--min-accuracy", type=float, help="exit 1 if overall accuracy is below this (0–1)")
    args = parser.parse_args(argv)

    try:
        extractor = get_extractor(args.extractor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    case_ids = [c.strip() for c in args.cases.split(",")] if args.cases else None
    cases, problems = load_cases(args.golden_dir, case_ids)
    for problem in problems:
        print(f"skipped {problem}", file=sys.stderr)
    if not cases:
        print("error: no verified golden cases to evaluate", file=sys.stderr)
        return 2

    print(f"Evaluating {extractor.name} on {len(cases)} case(s)\n")
    scores = run_eval(extractor, cases, args.delay, args.save_outputs)
    per_field, overall = summarise(scores)
    print("\n" + format_report(per_field, overall))

    if args.json_out:
        args.json_out.write_text(json.dumps(report_json(extractor.name, scores, per_field, overall), indent=2, default=str), encoding="utf-8")

    if args.min_accuracy is not None and (overall.accuracy or 0) < args.min_accuracy:
        print(f"\nFAIL: overall accuracy {_pct(overall.accuracy).strip()} is below {args.min_accuracy * 100:.1f}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The golden set: hand-checked notifications used to measure extraction accuracy.

Layout, one directory per case:
    tests/golden/<case_id>/notification.pdf
    tests/golden/<case_id>/expected.json        # hand-checked RecruitmentDraft
    tests/golden/<case_id>/expected.draft.json  # model output to correct (not scored)

Usage:
    python -m sarkari_crawler.golden new ssc-cgl-2026 ~/Downloads/cgl.pdf --extractor groq:<model>
    python -m sarkari_crawler.golden check
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from sarkari_core.rules import check_draft
from sarkari_core.schemas import RecruitmentDraft

DEFAULT_GOLDEN_DIR = Path("tests/golden")
PDF_NAME = "notification.pdf"
EXPECTED_NAME = "expected.json"
DRAFT_NAME = "expected.draft.json"


@dataclass
class GoldenCase:
    case_id: str
    pdf_path: Path
    expected: RecruitmentDraft


def load_cases(golden_dir: Path, case_ids: Optional[list[str]] = None) -> tuple[list[GoldenCase], list[str]]:
    """Return the verified cases and a list of problems (missing files, invalid JSON)."""
    cases, problems = [], []
    if not golden_dir.is_dir():
        return cases, [f"{golden_dir} does not exist"]
    for case_dir in sorted(p for p in golden_dir.iterdir() if p.is_dir()):
        if case_ids and case_dir.name not in case_ids:
            continue
        pdf, expected = case_dir / PDF_NAME, case_dir / EXPECTED_NAME
        if not expected.exists():
            problems.append(f"{case_dir.name}: no {EXPECTED_NAME} yet (not verified)")
            continue
        if not pdf.exists():
            problems.append(f"{case_dir.name}: {PDF_NAME} is missing")
            continue
        try:
            draft = RecruitmentDraft.model_validate_json(expected.read_text(encoding="utf-8"))
        except ValidationError as exc:
            problems.append(f"{case_dir.name}: {EXPECTED_NAME} is invalid: {exc.error_count()} errors")
            continue
        cases.append(GoldenCase(case_dir.name, pdf, draft))
    if case_ids:
        seen = {c.case_id for c in cases} | {p.split(":", 1)[0] for p in problems}
        problems += [f"{case_id}: not found" for case_id in sorted(set(case_ids) - seen)]
    return cases, problems


def _skeleton() -> dict:
    return {
        "organisation_name": "",
        "title": "",
        "posts": [],
    }


def cmd_new(args: argparse.Namespace) -> int:
    case_dir = args.golden_dir / args.case_id
    if case_dir.exists():
        print(f"{case_dir} already exists", file=sys.stderr)
        return 2
    source = Path(args.pdf).expanduser()
    case_dir.mkdir(parents=True)
    shutil.copyfile(source, case_dir / PDF_NAME)

    payload = _skeleton()
    if args.extractor:
        from .extractors import get_extractor
        from .pdf import prepare_document

        result = get_extractor(args.extractor).extract(prepare_document(source.read_bytes()))
        if result.draft is not None:
            payload = result.draft.model_dump(mode="json")
        for error in result.errors:
            print(f"extraction problem: {error}", file=sys.stderr)

    (case_dir / DRAFT_NAME).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Created {case_dir}. Check {DRAFT_NAME} against the PDF, fix it, then rename it to {EXPECTED_NAME}.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cases, problems = load_cases(args.golden_dir)
    for problem in problems:
        print(f"! {problem}")
    for case in cases:
        flags = check_draft(case.expected)
        status = "ok" if not flags else f"{len(flags)} rule flag(s), double-check these"
        print(f"{case.case_id}: {len(case.expected.posts)} posts, {status}")
        for flag in flags:
            print(f"    {flag.path}: {flag.message}")
    print(f"{len(cases)} verified case(s)")
    return 1 if any("invalid" in p for p in problems) else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sarkari_crawler.golden")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="add a notification to the golden set")
    new.add_argument("case_id", help="short id, e.g. ssc-cgl-2026")
    new.add_argument("pdf", help="path to the notification PDF")
    new.add_argument("--extractor", help="pre-fill the draft with this extractor spec")
    new.set_defaults(func=cmd_new)

    check = sub.add_parser("check", help="validate expected.json files and show rule flags")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

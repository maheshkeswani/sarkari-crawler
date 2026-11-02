"""Command-line entry point used by the GitHub Actions workflows.

    python -m sarkari_crawler.run crawl              # crawl due sources, extract, publish, close expired
    python -m sarkari_crawler.run crawl --no-extract # only look for new links
    python -m sarkari_crawler.run report --hours 24  # daily summary (stdout, job summary, Telegram)

Environment:
    SARKARI_DATABASE_URL   Supabase session-pooler URL for the sarkari_crawler role
    SARKARI_EXTRACTORS     e.g. groq:openai/gpt-oss-120b,openrouter:<model id>
    GROQ_API_KEY, OPENROUTER_API_KEY
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID                              (optional report)
    SARKARI_USER_AGENT                                                (optional)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

DATABASE_ENV = "SARKARI_DATABASE_URL"


def _connect(url: str):
    import psycopg

    # prepare_threshold=None: Supabase's poolers don't support server-side prepared statements.
    return psycopg.connect(url, autocommit=True, prepare_threshold=None, application_name="sarkari-crawler")


def cmd_crawl(args: argparse.Namespace) -> int:
    from .extractors import get_extractor
    from .fetch import USER_AGENT, Fetcher
    from .pipeline import Pipeline
    from .store import Store

    extractor = None
    if not args.no_extract:
        try:
            extractor = get_extractor(args.extractor)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    with _connect(os.environ[DATABASE_ENV]) as conn:
        pipeline = Pipeline(
            Store(conn),
            Fetcher(user_agent=os.environ.get("SARKARI_USER_AGENT") or USER_AGENT),
            extractor,
            log_line=print,
        )
        stats = pipeline.run(
            max_sources=args.max_sources,
            max_documents=0 if args.no_extract else args.max_documents,
            trigger=args.trigger,
        )
    print(
        f"\nsources {stats.sources_checked} checked, {stats.sources_failed} failed · {stats.links_found} new links · "
        f"{stats.documents_processed} extracted: {stats.published} published, {stats.corrigenda_applied} corrigenda, "
        f"{stats.duplicates} duplicates, {stats.failed} failed · {stats.closed} closed · {stats.tokens_used} tokens"
        + (" · LLM quota used up" if stats.quota_exhausted else "")
    )
    return 0


def format_report(summary: dict, hours: int) -> str:
    lines = [
        f"Sarkari crawler, last {hours} h",
        f"Runs: {summary['runs']} · sources checked {summary['sources_checked']} ({summary['sources_failed']} failed)",
        f"New links: {summary['links_found']} · published {summary['published']} · "
        f"corrigenda {summary['corrigenda_applied']} · not published {summary['failed']}",
        f"LLM tokens: {summary['tokens_used']:,}" + (" · free quota ran out" if summary["quota_exhausted"] else ""),
        f"Waiting for extraction: {summary['backlog']}",
    ]
    if summary["failing_sources"]:
        lines.append("Failing sources (3+ in a row):")
        lines += [f"  - {s['name']} ({s['failures']}x): {s['error']}" for s in summary["failing_sources"]]
    if summary["sources_without_links"]:
        lines.append("Sources with no notice links (need a link_selector or a custom parser):")
        lines += [f"  - {name}" for name in summary["sources_without_links"]]
    return "\n".join(lines)


def send_telegram(text: str, token: str, chat_id: str) -> None:
    import httpx

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def cmd_report(args: argparse.Namespace) -> int:
    from .store import Store

    with _connect(os.environ[DATABASE_ENV]) as conn:
        text = format_report(Store(conn).summary(args.hours), args.hours)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        send_telegram(text, token, chat_id)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m sarkari_crawler.run")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="crawl due sources, extract new notices and publish them")
    crawl.add_argument("--extractor", help="extractor spec; defaults to $SARKARI_EXTRACTORS")
    crawl.add_argument("--max-sources", type=int, default=40)
    crawl.add_argument("--max-documents", type=int, default=15, help="LLM extractions per run")
    crawl.add_argument("--no-extract", action="store_true", help="only look for new links")
    crawl.add_argument("--trigger", default="schedule", choices=["schedule", "manual"])
    crawl.set_defaults(func=cmd_crawl)

    report = sub.add_parser("report", help="print and send a summary of recent runs")
    report.add_argument("--hours", type=int, default=24)
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    if not os.environ.get(DATABASE_ENV):
        print(f"error: set {DATABASE_ENV}", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

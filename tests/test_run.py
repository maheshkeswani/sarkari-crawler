from sarkari_crawler.run import format_report, main


def test_report_lists_problems():
    summary = {
        "runs": 48, "sources_checked": 300, "sources_failed": 12, "links_found": 40, "published": 14,
        "corrigenda_applied": 2, "failed": 3, "tokens_used": 198500, "quota_exhausted": True, "backlog": 9,
        "failing_sources": [{"name": "UPSC", "failures": 6, "error": "HTTP 403"}],
        "sources_without_links": ["SSC – home page"],
    }
    text = format_report(summary, 24)
    assert "published 14" in text and "198,500" in text and "free quota ran out" in text
    assert "UPSC (6x): HTTP 403" in text and "SSC – home page" in text
    assert "Waiting for extraction: 9" in text


def test_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("SARKARI_DATABASE_URL", raising=False)
    assert main(["report"]) == 2
    assert "SARKARI_DATABASE_URL" in capsys.readouterr().err

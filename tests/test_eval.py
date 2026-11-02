import json

import pytest

from sarkari_core.extractor import ExtractionError, ExtractionResult
from sarkari_crawler import eval as eval_cli
from sarkari_crawler import golden
from sarkari_crawler.golden import load_cases
from tests.test_pdf import DATES_PAGE, TITLE_PAGE, VACANCY_PAGE


class EchoExtractor:
    """Returns a fixed draft, standing in for an LLM."""

    name = "fake:echo"
    model = "echo"

    def __init__(self, draft=None, error=None):
        self.draft, self.error = draft, error

    def extract(self, document):
        if self.error:
            raise self.error
        assert document.header_pages, "the PDF pipeline should have selected pages"
        return ExtractionResult("fake", "echo", "v1", draft=self.draft)


@pytest.fixture
def golden_dir(tmp_path, pdf_factory, sample_payload):
    verified = tmp_path / "ssc-cgl-2026"
    verified.mkdir()
    (verified / "notification.pdf").write_bytes(pdf_factory([TITLE_PAGE, DATES_PAGE, VACANCY_PAGE]))
    (verified / "expected.json").write_text(json.dumps(sample_payload))
    unverified = tmp_path / "rrb-ntpc-2026"
    unverified.mkdir()
    (unverified / "notification.pdf").write_bytes(pdf_factory([TITLE_PAGE]))
    return tmp_path


def test_load_cases_skips_unverified(golden_dir):
    cases, problems = load_cases(golden_dir)
    assert [c.case_id for c in cases] == ["ssc-cgl-2026"]
    assert problems == ["rrb-ntpc-2026: no expected.json yet (not verified)"]

    _, problems = load_cases(golden_dir, ["ssc-cgl-2026", "nope"])
    assert problems == ["nope: not found"]


def test_invalid_expected_json_is_reported(golden_dir):
    (golden_dir / "ssc-cgl-2026" / "expected.json").write_text(json.dumps({"title": "x"}))
    cases, problems = load_cases(golden_dir)
    assert cases == [] and "invalid" in problems[-1]


def test_run_eval_perfect_and_saves_outputs(golden_dir, sample_draft, tmp_path):
    cases, _ = load_cases(golden_dir)
    logs = []
    scores = eval_cli.run_eval(EchoExtractor(sample_draft), cases, save_outputs=tmp_path / "out", log=logs.append)
    assert eval_cli.summarise(scores)[1].accuracy == 1.0
    saved = json.loads((tmp_path / "out" / "ssc-cgl-2026.json").read_text())
    assert saved["draft"]["title"] == sample_draft.title and saved["rule_flags"] == []
    assert "100.0% fields correct" in logs[0]


def test_run_eval_records_provider_failure(golden_dir):
    cases, _ = load_cases(golden_dir)
    scores = eval_cli.run_eval(EchoExtractor(error=ExtractionError("quota")), cases, log=lambda _: None)
    assert scores[0].errors == ["quota"]
    assert eval_cli.summarise(scores)[1].correct == 0


def test_main_threshold_and_json_report(golden_dir, sample_draft, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eval_cli, "get_extractor", lambda spec: EchoExtractor(sample_draft))
    report = tmp_path / "report.json"
    args = ["--golden-dir", str(golden_dir), "--delay", "0", "--json-out", str(report), "--min-accuracy", "0.9"]
    assert eval_cli.main(args) == 0
    data = json.loads(report.read_text())
    assert data["overall"]["accuracy"] == 1.0 and data["cases"][0]["wrong_fields"] == []
    assert "OVERALL" in capsys.readouterr().out

    monkeypatch.setattr(eval_cli, "get_extractor", lambda spec: EchoExtractor(None))
    assert eval_cli.main(args) == 1


def test_main_without_cases_or_extractor(tmp_path, monkeypatch):
    monkeypatch.delenv("SARKARI_EXTRACTORS", raising=False)
    assert eval_cli.main(["--golden-dir", str(tmp_path)]) == 2
    monkeypatch.setattr(eval_cli, "get_extractor", lambda spec: EchoExtractor())
    assert eval_cli.main(["--golden-dir", str(tmp_path)]) == 2


def test_golden_new_and_check(tmp_path, pdf_factory, capsys):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(pdf_factory([TITLE_PAGE]))
    root = tmp_path / "golden"
    assert golden.main(["--golden-dir", str(root), "new", "upsc-cse-2026", str(pdf)]) == 0
    assert (root / "upsc-cse-2026" / "notification.pdf").exists()
    assert json.loads((root / "upsc-cse-2026" / "expected.draft.json").read_text())["posts"] == []
    assert golden.main(["--golden-dir", str(root), "new", "upsc-cse-2026", str(pdf)]) == 2

    assert golden.main(["--golden-dir", str(root), "check"]) == 0
    assert "0 verified case(s)" in capsys.readouterr().out

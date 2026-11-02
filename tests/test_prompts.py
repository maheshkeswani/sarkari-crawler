import pytest

from sarkari_core.enums import Discipline, FeeApplicant, QualificationLevel
from sarkari_core.prompts import PROMPT_VERSION, render


@pytest.mark.parametrize("step", ["header", "posts"])
def test_prompt_renders_without_leftover_placeholders(step):
    text = render(step, "NOTICE TEXT costs $100")
    assert "NOTICE TEXT costs $100" in text
    assert "$document_text" not in text
    assert "$" not in text.replace("$100", "")


def test_enum_values_come_from_core():
    header = render("header", "x")
    posts = render("posts", "x")
    assert all(f'"{m.value}"' in header for m in FeeApplicant)
    assert all(f'"{m.value}"' in posts for m in QualificationLevel)
    assert all(f'"{m.value}"' in posts for m in Discipline)


def test_prompt_version_has_files():
    render("header", "x", PROMPT_VERSION)
    render("posts", "x", PROMPT_VERSION)


def test_v2_prompt_asks_for_state_and_handles_hindi():
    from sarkari_core.enums import State

    header = render("header", "x")
    assert PROMPT_VERSION == "v2"
    assert all(f'"{m.value}"' in header for m in State)
    assert '"cancelled"' in header and '"title_hi"' in header and "Hindi" in header


def test_released_v1_prompts_still_render():
    assert "central government" in render("header", "x", "v1")
    assert "$" not in render("posts", "x", "v1")

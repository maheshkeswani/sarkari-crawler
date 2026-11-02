"""Versioned extraction prompts.

Never edit a released prompt file in place: copy it to the next version, change
PROMPT_VERSION, and let the eval compare the two. Enum values are injected here so
the prompts can't drift from sarkari_core.enums.
"""

from __future__ import annotations

from enum import Enum
from importlib import resources
from string import Template

from ..enums import (
    Discipline,
    DocumentKind,
    FeeApplicant,
    GenderRestriction,
    QualificationLevel,
    RelaxationCategory,
    ReservationCategory,
    State,
)

# v2: central and state notices, Hindi text, state / title_hi / cancelled fields.
PROMPT_VERSION = "v2"


def _values(enum: type[Enum]) -> str:
    return ", ".join(f'"{member.value}"' for member in enum)


_ENUM_VALUES = {
    "document_kinds": _values(DocumentKind),
    "fee_applicants": _values(FeeApplicant),
    "relaxation_categories": _values(RelaxationCategory),
    "reservation_categories": _values(ReservationCategory),
    "qualification_levels": _values(QualificationLevel),
    "disciplines": _values(Discipline),
    "genders": _values(GenderRestriction),
    "states": _values(State),
}


def render(step: str, document_text: str, version: str = PROMPT_VERSION) -> str:
    """Render the prompt for `step` ("header" or "posts")."""
    raw = resources.files(__package__).joinpath(f"{step}_{version}.txt").read_text(encoding="utf-8")
    return Template(raw).substitute(_ENUM_VALUES, document_text=document_text)

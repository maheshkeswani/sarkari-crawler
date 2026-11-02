"""Shared contract between sarkari-crawler and sarkari-web: schemas, rules, enums."""

from .schemas import SCHEMA_VERSION, PostDraft, RecruitmentDraft

__all__ = ["SCHEMA_VERSION", "PostDraft", "RecruitmentDraft"]

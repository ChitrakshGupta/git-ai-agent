"""
The structured review the agent must return. Enums stop the model from inventing categories.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    BUG = "bug"
    RISK = "risk"
    MISSING_TEST = "missing_test"
    STYLE = "style"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    category: Category = Field(
        description="bug = incorrect behaviour; risk = security, data loss, performance, breaking change; "
        "missing_test = changed logic with no test covering it; style = naming, readability, dead code."
    )
    severity: Severity
    file: str = Field(description="Path of the file exactly as listed in the PR.")
    line: int | None = Field(
        default=None,
        description="Line number in the NEW version of the file, taken from the numbered diff. "
        "Null for a file-level finding.",
    )
    title: str = Field(description="Short headline, under 80 characters.")
    explanation: str = Field(description="Why this is a problem, 1-3 sentences, referring to the actual code.")
    suggestion: str | None = Field(default=None, description="Concrete fix; a short code snippet is welcome.")


class Review(BaseModel):
    summary: str = Field(description="2-4 sentences: what the PR does and the overall verdict.")
    risk_level: Severity = Field(description="Overall risk of merging this PR as is.")
    findings: list[Finding] = Field(description="Empty list when nothing worth flagging was found.")

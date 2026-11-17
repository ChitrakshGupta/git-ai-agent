"""
ORM models: users (GitHub OAuth), repos (connected for auto-review), reviews (agent runs).

Column types are portable (Uuid / JSON) so the same models run on Postgres and SQLite.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    github_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(Text, nullable=False)
    access_token = Column(Text, nullable=False)  # GitHub OAuth token
    created_at = Column(DateTime(timezone=True), default=utcnow)

    repos = relationship("Repo", back_populates="user", cascade="all, delete-orphan")


class Repo(Base):
    __tablename__ = "repos"
    # One connection per repo across all users, so a webhook maps to exactly one token.
    __table_args__ = (UniqueConstraint("repo_full_name", name="uq_repo_full_name"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    repo_full_name = Column(Text, nullable=False)
    webhook_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="repos")


class Review(Base):
    __tablename__ = "reviews"
    # The same commit of the same PR is never reviewed twice per source. This also
    # absorbs GitHub webhook re-deliveries and double "synchronize" events.
    __table_args__ = (
        UniqueConstraint("repo_full_name", "pr_number", "head_sha", "source", name="uq_review_commit"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    repo_full_name = Column(Text, nullable=False)
    pr_number = Column(Integer, nullable=False)
    pr_title = Column(Text, nullable=True)
    pr_url = Column(Text, nullable=True)
    head_sha = Column(Text, nullable=False)
    source = Column(Text, nullable=False)  # "link" | "webhook"
    status = Column(Text, nullable=False, default="queued")  # queued | running | done | failed
    result = Column(JSON, nullable=True)  # Review JSON from the agent
    tool_trace = Column(JSON, nullable=True)  # list of tool calls, for the UI trace panel
    error = Column(Text, nullable=True)
    github_review_url = Column(Text, nullable=True)  # set once posted to the PR
    delivery_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)

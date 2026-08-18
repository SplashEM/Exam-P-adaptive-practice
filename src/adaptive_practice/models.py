"""In-memory domain values used by the learning engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from . import config


class UnderstandingRating(Enum):
    DIDNT_KNOW_GUESSED = 0.05
    PARTIALLY_KNEW = 0.40
    KNEW_HOW = 0.75
    FULLY_UNDERSTOOD = 1.00


class ErrorType(Enum):
    DIDNT_KNOW = "didnt_know"
    PARTIAL_SETUP = "partial_setup"
    EXECUTION_MISTAKE = "execution_mistake"
    UNCLASSIFIED = "unclassified"


@dataclass
class KnowledgeComponent:
    kc_id: str
    objective_mastery: float = config.P0
    understanding_score: float = 0.50
    displayed_mastery: float = config.P0
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    distinct_questions_attempted: int = 0
    meta_rating_count: int = 0
    last_attempt_at: datetime | None = None
    _attempted_question_ids: set[str] = field(default_factory=set, repr=False)

    def record_distinct_question(self, question_id: str) -> None:
        self._attempted_question_ids.add(question_id)
        self.distinct_questions_attempted = len(self._attempted_question_ids)


@dataclass
class Question:
    question_id: str
    primary_kc_id: str
    choice_count: int | None
    difficulty: int
    active: bool = True
    review_need: float = 0.50
    last_attempt_at: datetime | None = None
    last_attempt_correct: bool | None = None
    same_session_review: bool = False
    must_review_next_session: bool = False
    must_review_next_session_set_in_session_id: str | None = None


@dataclass
class SessionState:
    session_id: str
    question_history: list[str] = field(default_factory=list)
    attempt_counts: dict[str, int] = field(default_factory=dict)

    def record_appearance(self, question_id: str) -> None:
        self.question_history.append(question_id)

    def record_attempt(self, question_id: str) -> None:
        self.attempt_counts[question_id] = self.attempt_counts.get(question_id, 0) + 1

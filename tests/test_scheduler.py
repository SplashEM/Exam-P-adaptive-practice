from __future__ import annotations

from datetime import datetime, timedelta
import random

import pytest

from adaptive_practice import config
from adaptive_practice.models import KnowledgeComponent, Question, SessionState
from adaptive_practice.scheduler import is_eligible, question_weight, select_question


NOW = datetime(2026, 1, 15, 12, 0, 0)


def make_question(question_id: str, *, active: bool = True) -> Question:
    return Question(question_id, "kc1", choice_count=5, difficulty=3, active=active)


def test_question_in_previous_four_is_ineligible() -> None:
    question = make_question("q1")
    session = SessionState("s1", question_history=["q0", "q1", "q2", "q3"])
    assert is_eligible(question, session) is False


def test_question_with_three_session_attempts_is_ineligible() -> None:
    question = make_question("q1")
    session = SessionState("s1", attempt_counts={"q1": 3})
    assert is_eligible(question, session) is False


def test_weighted_selector_excludes_inactive_questions() -> None:
    inactive = make_question("inactive", active=False)
    active = make_question("active")
    kc = KnowledgeComponent("kc1")
    selected = select_question(
        [inactive, active], {"kc1": kc}, SessionState("s1"), NOW, rng=random.Random(3)
    )
    assert selected is active


def test_eligible_mastered_question_has_exploration_floor_weight() -> None:
    question = make_question("q1")
    kc = KnowledgeComponent("kc1", objective_mastery=1.0, understanding_score=1.0, displayed_mastery=1.0)
    question.review_need = 0.0
    question.last_attempt_at = NOW
    question.last_attempt_correct = True
    assert question_weight(question, kc, NOW) == pytest.approx(config.EXPLORATION_FLOOR)


def test_never_attempted_question_uses_maximum_recency() -> None:
    question = make_question("q1")
    kc = KnowledgeComponent("kc1")
    expected = config.EXPLORATION_FLOOR + 0.45 * (1 - kc.displayed_mastery) + 0.30 * question.review_need + 0.10
    assert question_weight(question, kc, NOW) == pytest.approx(expected)


def test_recency_is_capped_at_one() -> None:
    question = make_question("q1")
    question.last_attempt_at = NOW - timedelta(days=100)
    kc = KnowledgeComponent("kc1")
    old_weight = question_weight(question, kc, NOW)
    question.last_attempt_at = NOW - timedelta(days=config.RECENCY_HORIZON_DAYS)
    assert question_weight(question, kc, NOW) == pytest.approx(old_weight)

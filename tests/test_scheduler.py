from __future__ import annotations

from datetime import datetime, timedelta
import random

import pytest

from adaptive_practice import config
from adaptive_practice.mastery import update_objective_mastery
from adaptive_practice.models import KnowledgeComponent, Question, SessionState
from adaptive_practice.scheduler import (
    adaptive_mastery_multiplier,
    is_eligible,
    question_weight,
    select_question,
)


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


def test_evidence_qualified_mastered_question_retains_positive_weight() -> None:
    question = make_question("q1")
    kc = KnowledgeComponent(
        "kc1", objective_mastery=1.0, understanding_score=1.0, displayed_mastery=1.0,
        attempts=3, distinct_questions_attempted=2,
    )
    question.review_need = 0.0
    question.last_attempt_at = NOW
    question.last_attempt_correct = True
    assert question_weight(question, kc, NOW) == pytest.approx(
        config.EXPLORATION_FLOOR * config.ADAPTIVE_MULTIPLIER_MASTERED
    )
    assert question_weight(question, kc, NOW) > 0


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


def test_difficulty_has_no_effect_on_adaptive_weight() -> None:
    easy = make_question("easy")
    hard = make_question("hard")
    hard.difficulty = 5
    for question in (easy, hard):
        question.review_need = 0.35
        question.last_attempt_at = NOW - timedelta(days=7)
        question.last_attempt_correct = True
    kc = KnowledgeComponent("kc1", displayed_mastery=0.70)
    assert question_weight(easy, kc, NOW) == pytest.approx(question_weight(hard, kc, NOW))


@pytest.mark.parametrize(
    ("displayed_mastery", "attempts", "distinct_questions", "expected"),
    [
        (0.39, 0, 0, config.ADAPTIVE_MULTIPLIER_WEAK),
        (0.59, 0, 0, config.ADAPTIVE_MULTIPLIER_DEVELOPING),
        (0.60, 0, 0, config.ADAPTIVE_MULTIPLIER_COMPETENT),
        (0.80, 0, 0, config.ADAPTIVE_MULTIPLIER_STRONG),
        (0.95, 3, 2, config.ADAPTIVE_MULTIPLIER_MASTERED),
    ],
)
def test_adaptive_mastery_multiplier_uses_documented_bands(
    displayed_mastery: float, attempts: int, distinct_questions: int, expected: float
) -> None:
    kc = KnowledgeComponent(
        "kc1", displayed_mastery=displayed_mastery, attempts=attempts,
        distinct_questions_attempted=distinct_questions,
    )
    assert adaptive_mastery_multiplier(kc) == expected


def test_high_mastery_without_evidence_is_strong_not_mastered() -> None:
    kc = KnowledgeComponent("kc1", displayed_mastery=0.99, attempts=2, distinct_questions_attempted=1)
    assert adaptive_mastery_multiplier(kc) == config.ADAPTIVE_MULTIPLIER_STRONG


def test_adaptive_suppression_does_not_change_bkt_update() -> None:
    assert update_objective_mastery(0.35, True, 5) == pytest.approx(0.6987804878)

"""Adaptive weighting, eligibility, and weighted question selection."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Iterable, Sequence

from . import config
from .mastery import mastery_status
from .models import KnowledgeComponent, Question, SessionState


def adaptive_mastery_multiplier(kc: KnowledgeComponent) -> float:
    """Return ordinary adaptive suppression for the existing mastery band.

    ``mastery_status`` is the single source for the evidence-qualified
    ``Mastered`` condition. Required and carryover reviews never call this
    helper, so they remain authoritative regardless of the multiplier.
    """
    multipliers = {
        "Weak": config.ADAPTIVE_MULTIPLIER_WEAK,
        "Developing": config.ADAPTIVE_MULTIPLIER_DEVELOPING,
        "Competent": config.ADAPTIVE_MULTIPLIER_COMPETENT,
        "Strong": config.ADAPTIVE_MULTIPLIER_STRONG,
        "Mastered": config.ADAPTIVE_MULTIPLIER_MASTERED,
    }
    return multipliers[mastery_status(kc)]


def base_question_weight(question: Question, kc: KnowledgeComponent, now: datetime) -> float:
    """Return the original V1 adaptive weight before band suppression.

    A never-attempted question uses recency 1.0 (maximally stale), a simple
    deterministic treatment that permits it to receive normal consideration.
    """
    if question.last_attempt_at is None:
        recency = 1.0
    else:
        days_since_last_attempt = max(0.0, (now - question.last_attempt_at).total_seconds() / 86400)
        recency = min(1.0, days_since_last_attempt / config.RECENCY_HORIZON_DAYS)
    skill_gap = 1 - kc.displayed_mastery
    question_need = question.review_need
    recent_failure = 1.0 if question.last_attempt_correct is False else 0.0
    priority = (
        0.45 * skill_gap
        + 0.30 * question_need
        + 0.15 * recent_failure
        + 0.10 * recency
    )
    return config.EXPLORATION_FLOOR + priority


def question_weight(question: Question, kc: KnowledgeComponent, now: datetime) -> float:
    """Return the ordinary adaptive weight after mastery-band suppression."""
    return base_question_weight(question, kc, now) * adaptive_mastery_multiplier(kc)


def is_eligible(
    question: Question,
    session: SessionState,
    selected_question_ids: set[str] | None = None,
) -> bool:
    """Apply V1 adaptive eligibility constraints."""
    if not question.active:
        return False
    if selected_question_ids is not None and question.question_id not in selected_question_ids:
        return False
    if session.attempt_counts.get(question.question_id, 0) >= config.MAX_ATTEMPTS_PER_QUESTION_PER_SESSION:
        return False
    return question.question_id not in session.question_history[-config.MIN_INTERVENING_QUESTIONS :]


def select_question(
    questions: Iterable[Question],
    knowledge_components: dict[str, KnowledgeComponent],
    session: SessionState,
    now: datetime,
    *,
    selected_question_ids: set[str] | None = None,
    rng: random.Random | None = None,
) -> Question | None:
    """Select one eligible question proportionally to its positive V1 weight."""
    eligible = [
        question
        for question in questions
        if is_eligible(question, session, selected_question_ids)
    ]
    if not eligible:
        return None
    generator = rng or random.Random()
    weights = [question_weight(question, knowledge_components[question.primary_kc_id], now) for question in eligible]
    return generator.choices(eligible, weights=weights, k=1)[0]

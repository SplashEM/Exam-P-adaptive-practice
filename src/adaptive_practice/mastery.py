"""Mastery and review-need updates defined by the V1 specification."""

from __future__ import annotations

from datetime import datetime

from . import config
from .models import ErrorType, KnowledgeComponent, Question, SessionState, UnderstandingRating


def guess_probability(choice_count: int | None) -> float:
    """Return 1 / choice_count when valid, otherwise the documented default."""
    return 1 / choice_count if isinstance(choice_count, int) and choice_count > 0 else config.DEFAULT_PG


def update_objective_mastery(current: float, correct: bool, choice_count: int | None) -> float:
    """Apply one BKT observation followed by the learning transition."""
    pg = guess_probability(choice_count)
    if correct:
        posterior = current * (1 - config.PS) / (
            current * (1 - config.PS) + (1 - current) * pg
        )
    else:
        posterior = current * config.PS / (
            current * config.PS + (1 - current) * (1 - pg)
        )
    return posterior + (1 - posterior) * config.PL


def update_displayed_mastery(kc: KnowledgeComponent) -> float:
    if kc.meta_rating_count == 0:
        kc.displayed_mastery = kc.objective_mastery
    else:
        kc.displayed_mastery = (
            config.OBJECTIVE_WEIGHT * kc.objective_mastery
            + config.UNDERSTANDING_WEIGHT * kc.understanding_score
        )
    return kc.displayed_mastery


def mastery_status(kc: KnowledgeComponent) -> str:
    """Return the V1 mastery band, enforcing evidence for Mastered."""
    value = kc.displayed_mastery
    if (
        value >= config.MASTERY_THRESHOLD
        and kc.attempts >= config.MIN_MASTERY_ATTEMPTS
        and kc.distinct_questions_attempted >= config.MIN_DISTINCT_MASTERY_QUESTIONS
    ):
        return "Mastered"
    if value >= 0.80:
        return "Strong"
    if value >= 0.60:
        return "Competent"
    if value >= 0.40:
        return "Developing"
    return "Weak"


def review_severity(
    correct: bool,
    understanding_rating: UnderstandingRating | None,
    error_type: ErrorType | None,
) -> float:
    """Map an attempt's outcome to its specified question-review severity."""
    if correct:
        if understanding_rating is UnderstandingRating.DIDNT_KNOW_GUESSED:
            return 0.80
        if understanding_rating is UnderstandingRating.PARTIALLY_KNEW:
            return 0.55
        if understanding_rating is UnderstandingRating.KNEW_HOW:
            return 0.20
        if understanding_rating is UnderstandingRating.FULLY_UNDERSTOOD:
            return 0.05
        raise ValueError("a correct attempt requires an understanding rating")

    if error_type is ErrorType.DIDNT_KNOW:
        return 1.00
    if error_type is ErrorType.PARTIAL_SETUP:
        return 0.85
    if error_type is ErrorType.EXECUTION_MISTAKE:
        return 0.60
    return 0.90


def process_attempt(
    *,
    question: Question,
    kc: KnowledgeComponent,
    session: SessionState,
    correct: bool | None = None,
    understanding_rating: UnderstandingRating | None = None,
    error_type: ErrorType | None = None,
    attempted_at: datetime | None = None,
    response_time_ms: int | None = None,
    skipped: bool = False,
) -> None:
    """Apply one non-persistent attempt to its question and primary KC.

    ``response_time_ms`` is accepted as attempt metadata and intentionally has no
    effect on any learning or scheduling value. Skips leave all engine state
    unchanged, including session attempt counts.
    """
    del response_time_ms  # Explicitly excluded from V1 mastery and scheduling.
    if skipped:
        return
    if correct is None:
        raise ValueError("a non-skipped attempt requires correctness")

    when = attempted_at or datetime.now()
    kc.objective_mastery = update_objective_mastery(
        kc.objective_mastery, correct, question.choice_count
    )
    kc.attempts += 1
    if correct:
        kc.successes += 1
    else:
        kc.failures += 1
    kc.record_distinct_question(question.question_id)
    kc.last_attempt_at = when

    if understanding_rating is not None:
        kc.understanding_score += config.UNDERSTANDING_ALPHA * (
            understanding_rating.value - kc.understanding_score
        )
        kc.meta_rating_count += 1
    update_displayed_mastery(kc)

    severity = review_severity(correct, understanding_rating, error_type)
    question.review_need = (
        config.QUESTION_NEED_OLD_WEIGHT * question.review_need
        + config.QUESTION_NEED_NEW_WEIGHT * severity
    )
    question.last_attempt_at = when
    question.last_attempt_correct = correct
    session.record_attempt(question.question_id)
    session.record_appearance(question.question_id)

    if not correct or understanding_rating is UnderstandingRating.DIDNT_KNOW_GUESSED:
        question.same_session_review = True
        question.must_review_next_session = True
        question.must_review_next_session_set_in_session_id = session.session_id
    elif (
        understanding_rating is UnderstandingRating.PARTIALLY_KNEW
        and kc.displayed_mastery < 0.60
    ):
        question.same_session_review = True

    clear_next_session_review_if_earned(
        question=question,
        session_id=session.session_id,
        correct=correct,
        understanding_rating=understanding_rating,
    )


def clear_next_session_review_if_earned(
    *,
    question: Question,
    session_id: str,
    correct: bool,
    understanding_rating: UnderstandingRating | None,
) -> bool:
    """Clear future review only with later-session strong evidence."""
    if (
        question.must_review_next_session
        and question.must_review_next_session_set_in_session_id != session_id
        and correct
        and understanding_rating
        in {UnderstandingRating.KNEW_HOW, UnderstandingRating.FULLY_UNDERSTOOD}
    ):
        question.must_review_next_session = False
        return True
    return False

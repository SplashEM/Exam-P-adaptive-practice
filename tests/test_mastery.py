from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from adaptive_practice import config
from adaptive_practice.mastery import (
    clear_next_session_review_if_earned,
    mastery_status,
    process_attempt,
)
from adaptive_practice.models import (
    ErrorType,
    KnowledgeComponent,
    Question,
    SessionState,
    UnderstandingRating,
)


NOW = datetime(2026, 1, 1, 12, 0, 0)


def make_question(question_id: str = "q1") -> Question:
    return Question(question_id, "kc1", choice_count=5, difficulty=2)


def make_state(question_id: str = "q1") -> tuple[Question, KnowledgeComponent, SessionState]:
    return make_question(question_id), KnowledgeComponent("kc1"), SessionState("session-1")


def attempt(
    question: Question,
    kc: KnowledgeComponent,
    session: SessionState,
    *,
    correct: bool,
    rating: UnderstandingRating,
    error: ErrorType | None = None,
    at: datetime = NOW,
    response_time_ms: int | None = None,
) -> None:
    process_attempt(
        question=question,
        kc=kc,
        session=session,
        correct=correct,
        understanding_rating=rating,
        error_type=error,
        attempted_at=at,
        response_time_ms=response_time_ms,
    )


def test_correct_fully_understood_has_higher_displayed_mastery_than_guessed() -> None:
    full_q, full_kc, full_session = make_state()
    guess_q, guess_kc, guess_session = make_state()
    attempt(full_q, full_kc, full_session, correct=True, rating=UnderstandingRating.FULLY_UNDERSTOOD)
    attempt(guess_q, guess_kc, guess_session, correct=True, rating=UnderstandingRating.DIDNT_KNOW_GUESSED)
    assert full_kc.displayed_mastery > guess_kc.displayed_mastery


def test_wrong_knew_how_has_higher_displayed_mastery_than_didnt_know() -> None:
    knew_q, knew_kc, knew_session = make_state()
    didnt_q, didnt_kc, didnt_session = make_state()
    attempt(knew_q, knew_kc, knew_session, correct=False, rating=UnderstandingRating.KNEW_HOW, error=ErrorType.EXECUTION_MISTAKE)
    attempt(didnt_q, didnt_kc, didnt_session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert knew_kc.displayed_mastery > didnt_kc.displayed_mastery


def test_wrong_answer_increases_review_need() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert question.review_need > 0.50


def test_correct_fully_understood_decreases_review_need() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=True, rating=UnderstandingRating.FULLY_UNDERSTOOD)
    assert question.review_need < 0.50


def test_wrong_attempt_sets_both_review_flags() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert question.same_session_review is True
    assert question.must_review_next_session is True


def test_correct_answer_increases_objective_bkt_mastery() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=True, rating=UnderstandingRating.KNEW_HOW)
    assert kc.objective_mastery > config.P0


def test_incorrect_answer_normally_decreases_objective_bkt_mastery() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert kc.objective_mastery < config.P0


def test_repeated_correct_fully_understood_reaches_high_displayed_mastery() -> None:
    kc = KnowledgeComponent("kc1")
    session = SessionState("session-1")
    for index in range(8):
        question = make_question(f"q{index % 2}")
        attempt(question, kc, session, correct=True, rating=UnderstandingRating.FULLY_UNDERSTOOD, at=NOW + timedelta(minutes=index))
    assert kc.displayed_mastery >= 0.95


def test_mastered_requires_threshold_attempts_and_distinct_questions() -> None:
    kc = KnowledgeComponent("kc1", objective_mastery=0.99, understanding_score=1.0, displayed_mastery=0.99)
    assert mastery_status(kc) == "Strong"
    kc.attempts = 3
    assert mastery_status(kc) == "Strong"
    kc.distinct_questions_attempted = 2
    assert mastery_status(kc) == "Mastered"


def test_response_time_has_zero_effect_on_mastery() -> None:
    fast_q, fast_kc, fast_session = make_state()
    slow_q, slow_kc, slow_session = make_state()
    attempt(fast_q, fast_kc, fast_session, correct=True, rating=UnderstandingRating.KNEW_HOW, response_time_ms=1)
    attempt(slow_q, slow_kc, slow_session, correct=True, rating=UnderstandingRating.KNEW_HOW, response_time_ms=999_999)
    assert fast_kc.objective_mastery == pytest.approx(slow_kc.objective_mastery)
    assert fast_kc.understanding_score == pytest.approx(slow_kc.understanding_score)
    assert fast_kc.displayed_mastery == pytest.approx(slow_kc.displayed_mastery)
    assert fast_q.review_need == pytest.approx(slow_q.review_need)


def test_skip_does_not_change_engine_or_counters() -> None:
    question, kc, session = make_state()
    before = (kc.objective_mastery, kc.understanding_score, kc.displayed_mastery, kc.attempts, kc.successes, kc.failures, question.review_need)
    process_attempt(question=question, kc=kc, session=session, skipped=True)
    assert (kc.objective_mastery, kc.understanding_score, kc.displayed_mastery, kc.attempts, kc.successes, kc.failures, question.review_need) == before
    assert session.attempt_counts == {}


def test_correct_guessed_sets_next_session_review() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=True, rating=UnderstandingRating.DIDNT_KNOW_GUESSED)
    assert question.must_review_next_session is True


def test_next_session_flag_cannot_clear_during_same_session() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert clear_next_session_review_if_earned(question=question, session_id=session.session_id, correct=True, understanding_rating=UnderstandingRating.KNEW_HOW) is False
    assert question.must_review_next_session is True


def test_next_session_flag_clears_later_after_correct_knew_how() -> None:
    question, kc, session = make_state()
    attempt(question, kc, session, correct=False, rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert clear_next_session_review_if_earned(question=question, session_id="session-2", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW) is True
    assert question.must_review_next_session is False

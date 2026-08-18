from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adaptive_practice.models import ErrorType, KnowledgeComponent, Question, SessionState, UnderstandingRating
from adaptive_practice.persistence import SQLiteRepository


NOW = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)


def repository(tmp_path) -> SQLiteRepository:
    repo = SQLiteRepository(tmp_path / "study.sqlite")
    repo.initialize()
    return repo


def question(question_id: str = "q1") -> Question:
    return Question(
        question_id, "kc1", 5, 2, question_text="Question text",
        answer_choices=["A", "B", "C", "D", "E"], correct_answer="B",
        solution="Solution", topic="Probability", subtopic="Events",
    )


def seed(repo: SQLiteRepository, *questions: Question) -> None:
    repo.save_skill(KnowledgeComponent("kc1"))
    for item in questions:
        repo.save_question(item)


def record_wrong(repo: SQLiteRepository, session_id: str, question_id: str = "q1"):
    return repo.record_attempt(
        session_id=session_id, question_id=question_id, correct=False,
        understanding_rating=UnderstandingRating.DIDNT_KNOW_GUESSED,
        error_type=ErrorType.DIDNT_KNOW, started_at=NOW, submitted_at=NOW,
        response_time_ms=321, selected_answer="A", solution_viewed=True,
    )


def test_initialization_creates_required_tables(tmp_path) -> None:
    repo = repository(tmp_path)
    names = {row[0] for row in repo.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"questions", "skills", "sessions", "attempts", "kc_question_attempts"} <= names
    repo.close()


def test_initialization_is_idempotent_and_preserves_data(tmp_path) -> None:
    repo = repository(tmp_path)
    seed(repo, question())
    repo.initialize()
    assert repo.get_question("q1") is not None
    repo.close()


def test_question_persistence_round_trip(tmp_path) -> None:
    repo = repository(tmp_path)
    source = question()
    repo.save_question(source)
    loaded = repo.get_question("q1")
    assert loaded is not None
    assert (loaded.question_text, loaded.answer_choices, loaded.correct_answer, loaded.solution, loaded.topic, loaded.subtopic, loaded.difficulty, loaded.primary_kc_id) == (source.question_text, source.answer_choices, source.correct_answer, source.solution, source.topic, source.subtopic, source.difficulty, source.primary_kc_id)
    repo.close()


def test_question_bank_listing_returns_all_saved_questions(tmp_path) -> None:
    repo = repository(tmp_path)
    seed(repo, question("q2"), question("q1"))
    assert [item.question_id for item in repo.list_questions()] == ["q1", "q2"]
    repo.close()


def test_skill_persistence_round_trip(tmp_path) -> None:
    repo = repository(tmp_path)
    skill = KnowledgeComponent("kc1", objective_mastery=.72, understanding_score=.61, displayed_mastery=.6925, attempts=4, successes=3, failures=1, distinct_questions_attempted=2, meta_rating_count=4, last_attempt_at=NOW)
    repo.save_skill(skill)
    loaded = repo.get_skill("kc1")
    assert loaded is not None
    assert (loaded.objective_mastery, loaded.understanding_score, loaded.displayed_mastery, loaded.attempts, loaded.successes, loaded.failures, loaded.distinct_questions_attempted, loaded.meta_rating_count, loaded.last_attempt_at) == (skill.objective_mastery, skill.understanding_score, skill.displayed_mastery, skill.attempts, skill.successes, skill.failures, skill.distinct_questions_attempted, skill.meta_rating_count, skill.last_attempt_at)
    repo.close()


def test_attempt_history_is_append_only(tmp_path) -> None:
    repo = repository(tmp_path)
    seed(repo, question())
    session = repo.start_session(NOW)
    first = record_wrong(repo, session.session_id)
    second = repo.record_attempt(session_id=session.session_id, question_id="q1", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(minutes=1))
    attempts = repo.list_attempts_for_question("q1")
    assert [attempt.attempt_id for attempt in attempts] == [first.attempt_id, second.attempt_id]
    repo.close()


def test_session_can_be_created_loaded_and_completed(tmp_path) -> None:
    repo = repository(tmp_path)
    session = repo.start_session(NOW)
    assert repo.get_session(session.session_id) == session
    completed = repo.finish_session(session.session_id, NOW + timedelta(minutes=20))
    assert (completed.status, completed.ended_at) == ("COMPLETED", NOW + timedelta(minutes=20))
    repo.close()


def test_naive_timestamps_are_normalized_to_utc(tmp_path) -> None:
    repo = repository(tmp_path)
    naive = datetime(2026, 2, 1, 12, 0, 0)
    session = repo.start_session(naive)
    assert session.started_at.tzinfo == timezone.utc
    assert repo.get_session(session.session_id).started_at.tzinfo == timezone.utc
    repo.close()


def test_mastery_and_review_need_survive_restart(tmp_path) -> None:
    path = tmp_path / "study.sqlite"
    repo = SQLiteRepository(path); repo.initialize(); seed(repo, question())
    session = repo.start_session(NOW)
    record_wrong(repo, session.session_id)
    before_skill, before_question = repo.get_skill("kc1"), repo.get_question("q1")
    repo.close()
    reopened = SQLiteRepository(path); reopened.initialize()
    after_skill, after_question = reopened.get_skill("kc1"), reopened.get_question("q1")
    assert after_skill is not None and after_question is not None and before_skill is not None and before_question is not None
    assert after_skill.objective_mastery == pytest.approx(before_skill.objective_mastery)
    assert after_skill.understanding_score == pytest.approx(before_skill.understanding_score)
    assert after_skill.displayed_mastery == pytest.approx(before_skill.displayed_mastery)
    assert (after_skill.attempts, after_skill.successes, after_skill.failures,
            after_skill.meta_rating_count, after_skill.distinct_questions_attempted,
            after_skill.last_attempt_at) == (
                before_skill.attempts, before_skill.successes, before_skill.failures,
                before_skill.meta_rating_count, before_skill.distinct_questions_attempted,
                before_skill.last_attempt_at,
            )
    assert after_question.review_need == pytest.approx(before_question.review_need)
    reopened.close()


def test_next_session_flag_survives_restart(tmp_path) -> None:
    path = tmp_path / "study.sqlite"
    repo = SQLiteRepository(path); repo.initialize(); seed(repo, question())
    record_wrong(repo, repo.start_session(NOW).session_id); repo.close()
    reopened = SQLiteRepository(path); reopened.initialize()
    assert reopened.get_question("q1").must_review_next_session is True
    assert [item.question_id for item in reopened.list_questions_for_next_session_review()] == ["q1"]
    reopened.close()


def test_later_session_correct_knew_how_clears_persisted_future_review(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question())
    first = repo.start_session(NOW); record_wrong(repo, first.session_id); repo.finish_session(first.session_id)
    second = repo.start_session(NOW + timedelta(days=1))
    repo.record_attempt(session_id=second.session_id, question_id="q1", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(days=1))
    assert repo.get_question("q1").must_review_next_session is False
    repo.close()


def test_same_session_state_is_not_durable(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question())
    session = repo.start_session(NOW)
    state = SessionState(session.session_id)
    repo.record_attempt(session_id=session.session_id, question_id="q1", correct=False,
                        understanding_rating=UnderstandingRating.DIDNT_KNOW_GUESSED,
                        error_type=ErrorType.DIDNT_KNOW, submitted_at=NOW, session_state=state)
    assert state.question_history == ["q1"]
    assert repo.get_question("q1").same_session_review is False
    repo.close()


def test_distinct_question_count_does_not_inflate_after_restart(tmp_path) -> None:
    path = tmp_path / "study.sqlite"
    repo = SQLiteRepository(path); repo.initialize(); seed(repo, question())
    session = repo.start_session(NOW)
    record_wrong(repo, session.session_id); record_wrong(repo, session.session_id)
    assert repo.get_skill("kc1").distinct_questions_attempted == 1
    repo.close()
    reopened = SQLiteRepository(path); reopened.initialize()
    assert reopened.get_skill("kc1").distinct_questions_attempted == 1
    reopened.close()


def test_distinct_question_count_increments_for_two_questions(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question("q1"), question("q2"))
    session = repo.start_session(NOW)
    record_wrong(repo, session.session_id, "q1"); record_wrong(repo, session.session_id, "q2")
    assert repo.get_skill("kc1").distinct_questions_attempted == 2
    repo.close()


def test_atomic_attempt_write_rolls_back_on_insert_failure(tmp_path, monkeypatch) -> None:
    repo = repository(tmp_path); seed(repo, question()); session = repo.start_session(NOW)
    before_skill, before_question = repo.get_skill("kc1"), repo.get_question("q1")
    def fail(_record):
        raise sqlite3.OperationalError("forced write failure")
    import sqlite3
    monkeypatch.setattr(repo, "_insert_attempt", fail)
    with pytest.raises(sqlite3.OperationalError):
        record_wrong(repo, session.session_id)
    assert repo.list_attempts_for_question("q1") == []
    assert repo.get_skill("kc1").objective_mastery == pytest.approx(before_skill.objective_mastery)
    assert repo.get_question("q1").review_need == pytest.approx(before_question.review_need)
    repo.close()


def test_response_time_persists(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question()); session = repo.start_session(NOW)
    record = record_wrong(repo, session.session_id)
    assert repo.list_attempts_for_question("q1")[0].response_time_ms == record.response_time_ms == 321
    repo.close()


def test_skip_is_persisted_without_changing_mastery_or_counters(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question()); session = repo.start_session(NOW)
    before = repo.get_skill("kc1")
    record = repo.record_attempt(session_id=session.session_id, question_id="q1", skipped=True, submitted_at=NOW)
    after = repo.get_skill("kc1")
    assert record.skipped is True and record.correct is None
    assert (after.objective_mastery, after.attempts, after.successes, after.failures) == (before.objective_mastery, before.attempts, before.successes, before.failures)
    repo.close()


def test_attempt_ordering_and_question_history(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question()); session = repo.start_session(NOW)
    first = record_wrong(repo, session.session_id)
    second = repo.record_attempt(session_id=session.session_id, question_id="q1", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(seconds=1))
    attempts = repo.list_attempts_for_question("q1")
    assert [item.attempt_id for item in attempts] == [first.attempt_id, second.attempt_id]
    assert [item.correct for item in attempts] == [False, True]
    repo.close()


def test_attempt_before_after_values_remain_historical(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question()); session = repo.start_session(NOW)
    first = record_wrong(repo, session.session_id)
    repo.record_attempt(session_id=session.session_id, question_id="q1", correct=True,
                        understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(minutes=1))
    historical_first = repo.list_attempts_for_question("q1")[0]
    assert historical_first.objective_mastery_before == pytest.approx(first.objective_mastery_before)
    assert historical_first.objective_mastery_after == pytest.approx(first.objective_mastery_after)
    assert historical_first.question_review_need_after == pytest.approx(first.question_review_need_after)
    repo.close()


def test_session_attempt_history_is_scoped_to_session(tmp_path) -> None:
    repo = repository(tmp_path); seed(repo, question())
    first, second = repo.start_session(NOW), repo.start_session(NOW + timedelta(days=1))
    record_wrong(repo, first.session_id)
    repo.record_attempt(session_id=second.session_id, question_id="q1", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(days=1))
    assert len(repo.list_attempts_for_session(first.session_id)) == 1
    assert len(repo.list_attempts_for_session(second.session_id)) == 1
    repo.close()


def test_end_to_end_progress_survives_restart_and_future_review_clears(tmp_path) -> None:
    path = tmp_path / "study.sqlite"
    repo = SQLiteRepository(path); repo.initialize(); seed(repo, question())
    first = repo.start_session(NOW); failure = record_wrong(repo, first.session_id); repo.finish_session(first.session_id); repo.close()
    reopened = SQLiteRepository(path); reopened.initialize()
    skill, stored_question = reopened.get_skill("kc1"), reopened.get_question("q1")
    assert len(reopened.list_attempts_for_question("q1")) == 1
    assert skill.objective_mastery < .35 and stored_question.review_need > .50 and stored_question.must_review_next_session
    second = reopened.start_session(NOW + timedelta(days=1))
    success = reopened.record_attempt(session_id=second.session_id, question_id="q1", correct=True, understanding_rating=UnderstandingRating.KNEW_HOW, submitted_at=NOW + timedelta(days=1))
    assert len(reopened.list_attempts_for_question("q1")) == 2
    assert success.objective_mastery_after > failure.objective_mastery_after
    assert reopened.get_question("q1").must_review_next_session is False
    reopened.close()

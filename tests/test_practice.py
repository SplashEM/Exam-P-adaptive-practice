from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random

import pytest

import adaptive_practice.practice as practice_module
from adaptive_practice.models import ErrorType, KnowledgeComponent, Question, UnderstandingRating
from adaptive_practice.persistence import SQLiteRepository
from adaptive_practice.practice import PracticePhase, PracticeSessionController


NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def make_question(question_id: str, difficulty: int, *, kc: str = "kc1", topic: str = "T", subtopic: str = "S", active: bool = True) -> Question:
    return Question(question_id, kc, 4, difficulty, active=active, question_text=question_id,
                    answer_choices=["A", "B", "C", "D"], correct_answer="B", solution="solution",
                    topic=topic, subtopic=subtopic)


def repo_with_questions(tmp_path, questions: list[Question]) -> SQLiteRepository:
    repo = SQLiteRepository(tmp_path / "study.sqlite"); repo.initialize()
    for kc in {question.primary_kc_id for question in questions}:
        repo.save_skill(KnowledgeComponent(kc))
    for question in questions:
        repo.save_question(question)
    return repo


def submit(controller: PracticeSessionController, question: Question, *, answer: str = "B", rating=UnderstandingRating.KNEW_HOW, error=None, skipped=False):
    return controller.submit_attempt(question.question_id, selected_answer=answer, understanding_rating=rating,
                                     error_type=error, response_time_ms=123, solution_viewed=True,
                                     skipped=skipped, submitted_at=NOW)


def test_initial_pass_is_easy_to_hard_and_has_no_repeats(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q3", 3), make_question("q1", 1), make_question("q2", 2)])
    controller = PracticeSessionController(repo, rng=random.Random(2), started_at=NOW)
    shown = []
    for _ in range(3):
        question = controller.next_question(); shown.append(question.question_id); submit(controller, question)
    assert shown == ["q1", "q2", "q3"]
    assert len(set(shown)) == 3
    repo.close()


def test_equal_difficulty_order_is_seeded_shuffle(tmp_path) -> None:
    questions = [make_question(f"q{i}", 1) for i in range(4)]
    repo = repo_with_questions(tmp_path, questions)
    first = PracticeSessionController(repo, rng=random.Random(7), started_at=NOW)
    order1 = [first.next_question().question_id]
    submit(first, first.next_question()); order1 += [first.next_question().question_id]
    first.finish()
    second = PracticeSessionController(repo, rng=random.Random(7), started_at=NOW + timedelta(days=1))
    # Existing questions are no longer first-exposure items, so use an independent seeded bank.
    repo.close()
    other_path = tmp_path / "other"; other_path.mkdir()
    other = repo_with_questions(other_path, questions)
    repeat = PracticeSessionController(other, rng=random.Random(7), started_at=NOW)
    order2 = [repeat.next_question().question_id]
    submit(repeat, repeat.next_question()); order2 += [repeat.next_question().question_id]
    assert order1 == order2 and order1 != ["q0", "q1"]
    other.close()


def test_wrong_question_returns_after_four_intervening_questions(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question(f"q{i}", i) for i in range(1, 6)])
    controller = PracticeSessionController(repo, rng=random.Random(1), started_at=NOW)
    first = controller.next_question(); submit(controller, first, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    for _ in range(4):
        question = controller.next_question(); assert question.question_id != first.question_id; submit(controller, question)
    review = controller.next_question()
    assert controller.phase is PracticePhase.REQUIRED_REVIEW and review.question_id == first.question_id
    repo.close()


def test_correct_guessed_and_partial_low_mastery_create_required_review(tmp_path) -> None:
    partial_question = make_question("q2", 2, kc="kc2")
    partial_question.answer_choices = ["A", "B"]; partial_question.choice_count = 2
    repo = repo_with_questions(tmp_path, [make_question("q1", 1), partial_question])
    controller = PracticeSessionController(repo, started_at=NOW)
    q1 = controller.next_question(); guessed = submit(controller, q1, rating=UnderstandingRating.DIDNT_KNOW_GUESSED)
    q2 = controller.next_question(); partial = submit(controller, q2, rating=UnderstandingRating.PARTIALLY_KNEW)
    assert guessed.same_session_review_required and partial.same_session_review_required
    repo.close()


def test_required_review_resolves_and_transitions_to_adaptive(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question(f"q{i}", i) for i in range(1, 6)])
    controller = PracticeSessionController(repo, started_at=NOW)
    first = controller.next_question(); submit(controller, first, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    for _ in range(4): submit(controller, controller.next_question())
    review = controller.next_question(); result = submit(controller, review, rating=UnderstandingRating.KNEW_HOW)
    assert result.same_session_review_required is False and controller.phase is PracticePhase.ADAPTIVE_REVIEW
    repo.close()


def test_filters_and_inactive_questions(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("a", 1, topic="A", subtopic="one"), make_question("b", 1, topic="B", subtopic="one"), make_question("c", 1, topic="A", subtopic="two", active=False)])
    assert PracticeSessionController(repo, topic="A", started_at=NOW).pool_ids == {"a"}
    assert PracticeSessionController(repo, subtopic="one", started_at=NOW).pool_ids == {"a", "b"}
    assert PracticeSessionController(repo, question_ids={"b", "c"}, started_at=NOW).pool_ids == {"b"}
    repo.close()


def test_double_submit_is_blocked_and_persistence_is_used(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1)])
    controller = PracticeSessionController(repo, started_at=NOW); question = controller.next_question()
    submit(controller, question)
    with pytest.raises(ValueError): submit(controller, question)
    assert len(repo.list_attempts_for_session(controller.session.session_id)) == 1
    assert repo.get_skill("kc1").attempts == 1
    repo.close()


def test_skip_advances_without_changing_mastery(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1), make_question("q2", 2)])
    controller = PracticeSessionController(repo, started_at=NOW); first = controller.next_question()
    before = repo.get_skill("kc1").objective_mastery
    submit(controller, first, skipped=True)
    assert repo.get_skill("kc1").objective_mastery == before
    assert controller.next_question().question_id != first.question_id
    repo.close()


def test_finish_completes_persisted_session_and_stops_questions(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1)])
    controller = PracticeSessionController(repo, started_at=NOW); controller.finish(NOW + timedelta(minutes=1))
    assert repo.get_session(controller.session.session_id).status == "COMPLETED"
    assert controller.next_question() is None
    repo.close()


def test_carryover_restart_is_early_not_duplicated_and_clears_on_later_success(tmp_path) -> None:
    path = tmp_path / "study.sqlite"
    repo = SQLiteRepository(path); repo.initialize()
    for kc in ("kc1", "kc2"): repo.save_skill(KnowledgeComponent(kc))
    for question in [make_question("weak", 3, kc="kc1"), make_question("new", 1, kc="kc2"), make_question("other", 2, kc="kc1")]: repo.save_question(question)
    first = PracticeSessionController(repo, started_at=NOW)
    weak = first.next_question(); assert weak.question_id == "new"
    submit(first, weak)
    other = first.next_question(); submit(first, other)
    weak = first.next_question(); submit(first, weak, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    first.finish(); repo.close()
    reopened = SQLiteRepository(path); reopened.initialize()
    second = PracticeSessionController(reopened, started_at=NOW + timedelta(days=1))
    carryover = second.next_question()
    assert carryover.question_id == "weak" and list(second._coverage_queue).count("weak") == 0
    submit(second, carryover, rating=UnderstandingRating.KNEW_HOW)
    assert reopened.get_question("weak").must_review_next_session is False
    assert len(reopened.list_attempts_for_question("weak")) == 2
    reopened.close()


def test_same_session_success_does_not_clear_future_flag(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question(f"q{i}", i) for i in range(1, 6)])
    controller = PracticeSessionController(repo, started_at=NOW)
    first = controller.next_question(); submit(controller, first, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    for _ in range(4): submit(controller, controller.next_question())
    submit(controller, controller.next_question(), rating=UnderstandingRating.KNEW_HOW)
    assert repo.get_question(first.question_id).must_review_next_session is True
    repo.close()


def test_two_question_pool_relaxes_cooldown_without_back_to_back_review(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1), make_question("q2", 2)])
    controller = PracticeSessionController(repo, started_at=NOW)
    first = controller.next_question(); submit(controller, first, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    second = controller.next_question(); submit(controller, second)
    review = controller.next_question()
    assert review.question_id == first.question_id
    assert review.question_id != second.question_id
    repo.close()


def test_one_question_pool_terminates_without_back_to_back_repeat(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1)])
    controller = PracticeSessionController(repo, started_at=NOW)
    question = controller.next_question(); submit(controller, question, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED, error=ErrorType.DIDNT_KNOW)
    assert controller.next_question() is None
    assert controller.phase is PracticePhase.COMPLETED
    repo.close()


def test_completed_controller_rejects_submit(tmp_path) -> None:
    repo = repo_with_questions(tmp_path, [make_question("q1", 1)])
    controller = PracticeSessionController(repo, started_at=NOW); question = controller.next_question(); controller.finish()
    with pytest.raises(RuntimeError):
        submit(controller, question)
    repo.close()


def test_adaptive_phase_delegates_selection_to_existing_scheduler(tmp_path, monkeypatch) -> None:
    repo = repo_with_questions(tmp_path, [make_question(f"q{i}", i) for i in range(1, 6)])
    controller = PracticeSessionController(repo, started_at=NOW)
    for _ in range(5): submit(controller, controller.next_question())
    called = {}
    def choose(questions, skills, state, now, **kwargs):
        called["used"] = True
        return next(question for question in questions if question.question_id == "q1")
    monkeypatch.setattr(practice_module, "select_question", choose)
    assert controller.next_question().question_id == "q1"
    assert called == {"used": True}
    repo.close()


def test_required_review_is_not_suppressed_for_a_historically_strong_question(tmp_path) -> None:
    questions = [make_question(f"q{i}", i) for i in range(1, 6)]
    repo = repo_with_questions(tmp_path, questions)
    repo.save_skill(KnowledgeComponent("kc1", objective_mastery=0.99, understanding_score=1.0,
                                       displayed_mastery=0.99, attempts=3,
                                       distinct_questions_attempted=2))
    controller = PracticeSessionController(repo, started_at=NOW)
    strong = controller.next_question()
    assert strong.question_id == "q1"
    submit(controller, strong, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED,
           error=ErrorType.DIDNT_KNOW)
    for _ in range(4):
        submit(controller, controller.next_question())
    review = controller.next_question()
    assert review.question_id == strong.question_id
    assert controller.phase is PracticePhase.REQUIRED_REVIEW
    repo.close()


def test_carryover_is_not_suppressed_for_an_evidence_qualified_mastered_question(tmp_path) -> None:
    question = make_question("carryover", 3)
    question.must_review_next_session = True
    question.must_review_next_session_set_in_session_id = "previous-session"
    repo = repo_with_questions(tmp_path, [question])
    repo.save_skill(KnowledgeComponent("kc1", objective_mastery=0.99, understanding_score=1.0,
                                       displayed_mastery=0.99, attempts=3,
                                       distinct_questions_attempted=2))
    controller = PracticeSessionController(repo, started_at=NOW)
    assert controller.next_question().question_id == "carryover"
    repo.close()


def test_coverage_shows_every_selected_question_once_before_required_review(tmp_path) -> None:
    questions = [make_question("q1", 1), make_question("q2", 1), make_question("q3", 2),
                 make_question("q4", 3), make_question("q5", 4), make_question("q6", 5)]
    repo = repo_with_questions(tmp_path, questions)
    controller = PracticeSessionController(repo, rng=random.Random(9), started_at=NOW)
    shown = []
    for index in range(6):
        question = controller.next_question()
        shown.append(question.question_id)
        if index == 0:
            submit(controller, question, answer="A", rating=UnderstandingRating.DIDNT_KNOW_GUESSED,
                   error=ErrorType.DIDNT_KNOW)
        else:
            submit(controller, question)
    assert set(shown) == {question.question_id for question in questions}
    assert len(shown) == len(set(shown))
    review = controller.next_question()
    assert controller.phase is PracticePhase.REQUIRED_REVIEW
    assert review.question_id == shown[0]
    repo.close()


def test_fresh_coverage_is_easy_to_hard_with_seeded_equal_difficulty_shuffle(tmp_path) -> None:
    questions = [make_question("q1", 1), make_question("q2", 1), make_question("q3", 2),
                 make_question("q4", 3), make_question("q5", 4), make_question("q6", 5)]
    repo = repo_with_questions(tmp_path, questions)
    controller = PracticeSessionController(repo, rng=random.Random(7), started_at=NOW)
    shown = []
    for _ in questions:
        question = controller.next_question(); shown.append(question)
        submit(controller, question)
    assert [question.difficulty for question in shown] == [1, 1, 2, 3, 4, 5]
    assert {question.question_id for question in shown[:2]} == {"q1", "q2"}
    repo.close()


def test_later_session_covers_every_selected_question_again(tmp_path) -> None:
    questions = [make_question("q1", 1), make_question("q2", 2), make_question("q3", 3),
                 make_question("q4", 4), make_question("q5", 5)]
    repo = repo_with_questions(tmp_path, questions)
    first = PracticeSessionController(repo, rng=random.Random(3), started_at=NOW)
    for _ in questions:
        submit(first, first.next_question())
    first.finish()
    second = PracticeSessionController(repo, rng=random.Random(4), started_at=NOW + timedelta(days=1))
    shown = []
    for _ in questions:
        question = second.next_question(); shown.append(question.question_id)
        submit(second, question)
    assert set(shown) == {question.question_id for question in questions}
    assert len(shown) == len(set(shown))
    repo.close()


def test_mastered_question_receives_coverage_before_adaptive_suppression(tmp_path) -> None:
    questions = [make_question("mastered", 1), make_question("other", 2)]
    repo = repo_with_questions(tmp_path, questions)
    repo.save_skill(KnowledgeComponent("kc1", objective_mastery=.99, understanding_score=1.0,
                                       displayed_mastery=.99, attempts=3,
                                       distinct_questions_attempted=2))
    controller = PracticeSessionController(repo, started_at=NOW)
    shown = []
    for _ in questions:
        question = controller.next_question(); shown.append(question.question_id)
        submit(controller, question)
    assert "mastered" in shown
    assert len(shown) == len(set(shown))
    repo.close()

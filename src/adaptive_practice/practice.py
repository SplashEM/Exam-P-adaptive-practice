"""Programmatic practice-session orchestration for the MVP."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from . import config
from .models import ErrorType, Question, SessionState, UnderstandingRating
from .persistence import PracticeSession, SQLiteRepository, utc_now
from .scheduler import is_eligible, select_question


class PracticePhase(Enum):
    INITIAL_PASS = "INITIAL_PASS"
    REQUIRED_REVIEW = "REQUIRED_REVIEW"
    ADAPTIVE_REVIEW = "ADAPTIVE_REVIEW"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class SubmissionResult:
    correct: bool | None
    displayed_mastery: float
    review_need: float
    same_session_review_required: bool
    must_review_next_session: bool


class PracticeSessionController:
    """A small controller joining question selection, engine updates, and SQLite."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        topic: str | None = None,
        subtopic: str | None = None,
        question_ids: set[str] | None = None,
        rng: random.Random | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.repository = repository
        self.rng = rng or random.Random()
        self.session: PracticeSession = repository.start_session(started_at)
        self.session_state = SessionState(self.session.session_id)
        self.phase = PracticePhase.INITIAL_PASS
        self._shown_question_id: str | None = None
        self._shown_at: datetime | None = None
        self._required_reviews: dict[str, int] = {}
        self.pool = self._filtered_pool(topic, subtopic, question_ids)
        self.pool_ids = {question.question_id for question in self.pool}
        carryover_ids = {
            question.question_id
            for question in self.pool
            if question.must_review_next_session
        }
        carryover = [question for question in self.pool if question.question_id in carryover_ids]
        new_questions = [
            question for question in self.pool
            if question.question_id not in carryover_ids and question.last_attempt_at is None
        ]
        # Carryovers are early; within each group, equal difficulty is shuffled.
        self._initial_queue = self._difficulty_order(carryover) + self._difficulty_order(new_questions)

    def next_question(self) -> Question | None:
        """Return the current displayed question or choose the next eligible one."""
        if self.phase is PracticePhase.COMPLETED:
            return None
        if self._shown_question_id is not None:
            return self.repository.get_question(self._shown_question_id)

        if self.phase is PracticePhase.INITIAL_PASS:
            if self._initial_queue:
                return self._show(self._initial_queue.pop(0))
            self.phase = PracticePhase.REQUIRED_REVIEW

        if self.phase is PracticePhase.REQUIRED_REVIEW:
            required = self._next_required()
            if required is not None:
                return self._show(required.question_id)
            if not self._required_reviews:
                self.phase = PracticePhase.ADAPTIVE_REVIEW
            else:
                # Required items are cooling down. Use eligible adaptive material as intervening work.
                filler = self._next_adaptive()
                if filler is not None:
                    return self._show(filler.question_id)
                return self._complete_when_empty()

        if self.phase is PracticePhase.ADAPTIVE_REVIEW:
            adaptive = self._next_adaptive()
            if adaptive is not None:
                return self._show(adaptive.question_id)
            return self._complete_when_empty()
        return None

    def submit_attempt(
        self,
        question_id: str,
        *,
        selected_answer: str | None = None,
        understanding_rating: UnderstandingRating | None = None,
        error_type: ErrorType | None = None,
        response_time_ms: int | None = None,
        solution_viewed: bool = False,
        skipped: bool = False,
        submitted_at: datetime | None = None,
    ) -> SubmissionResult:
        """Persist a single shown attempt and update only session-local scheduling."""
        if self.phase is PracticePhase.COMPLETED:
            raise RuntimeError("session is completed")
        if self._shown_question_id != question_id:
            raise ValueError("question was not the currently displayed question")
        if question_id not in self.pool_ids:
            raise ValueError("question is outside the active practice pool")
        question = self.repository.get_question(question_id)
        assert question is not None
        correct = None if skipped else selected_answer == question.correct_answer
        self._required_reviews.pop(question_id, None)
        self.repository.record_attempt(
            session_id=self.session.session_id, question_id=question_id, correct=correct,
            understanding_rating=understanding_rating, error_type=error_type,
            started_at=self._shown_at, submitted_at=submitted_at, response_time_ms=response_time_ms,
            selected_answer=selected_answer, solution_viewed=solution_viewed, skipped=skipped,
            session_state=self.session_state,
        )
        if skipped:
            # The engine correctly leaves skips untouched; an appearance still needs cooldown history.
            self.session_state.record_appearance(question_id)
        updated_question = self.repository.get_question(question_id)
        skill = self.repository.get_skill(question.primary_kc_id)
        assert updated_question is not None and skill is not None
        if self._requires_same_session_review(correct, understanding_rating, skill.displayed_mastery):
            if self.session_state.attempt_counts.get(question_id, 0) < config.MAX_ATTEMPTS_PER_QUESTION_PER_SESSION:
                self._required_reviews[question_id] = self._review_priority(correct, understanding_rating, error_type)
        self._shown_question_id = None
        self._shown_at = None
        if self.phase is PracticePhase.REQUIRED_REVIEW and not self._required_reviews:
            self.phase = PracticePhase.ADAPTIVE_REVIEW
        return SubmissionResult(
            correct=correct, displayed_mastery=skill.displayed_mastery,
            review_need=updated_question.review_need,
            same_session_review_required=question_id in self._required_reviews,
            must_review_next_session=updated_question.must_review_next_session,
        )

    def finish(self, ended_at: datetime | None = None) -> PracticeSession:
        if self.phase is not PracticePhase.COMPLETED:
            self.session = self.repository.finish_session(self.session.session_id, ended_at)
            self.phase = PracticePhase.COMPLETED
            self._shown_question_id = None
        return self.session

    def _filtered_pool(self, topic: str | None, subtopic: str | None, ids: set[str] | None) -> list[Question]:
        return [
            question for question in self.repository.list_questions()
            if question.active
            and (topic is None or question.topic == topic)
            and (subtopic is None or question.subtopic == subtopic)
            and (ids is None or question.question_id in ids)
        ]

    def _difficulty_order(self, questions: list[Question]) -> list[str]:
        result: list[str] = []
        for difficulty in sorted({question.difficulty for question in questions}):
            group = [question.question_id for question in questions if question.difficulty == difficulty]
            self.rng.shuffle(group)
            result.extend(group)
        return result

    def _show(self, question_id: str) -> Question:
        self._shown_question_id = question_id
        self._shown_at = utc_now()
        question = self.repository.get_question(question_id)
        assert question is not None
        return question

    def _next_required(self) -> Question | None:
        eligible = [
            self.repository.get_question(question_id)
            for question_id in self._required_reviews
        ]
        candidates = [
            question for question in eligible
            if question is not None and is_eligible(question, self.session_state, self.pool_ids)
        ]
        if not candidates and len(self.pool) <= config.MIN_INTERVENING_QUESTIONS:
            # Small pools cannot always supply four intervening questions. Relax
            # only that window, never the no-back-to-back or attempt-cap rules.
            last_id = self.session_state.question_history[-1] if self.session_state.question_history else None
            candidates = [
                question for question in eligible
                if question is not None
                and question.question_id != last_id
                and self.session_state.attempt_counts.get(question.question_id, 0) < config.MAX_ATTEMPTS_PER_QUESTION_PER_SESSION
            ]
        if not candidates:
            return None
        return min(candidates, key=lambda question: self._required_reviews[question.question_id])

    def _next_adaptive(self) -> Question | None:
        questions = [self.repository.get_question(question_id) for question_id in self.pool_ids]
        active = [question for question in questions if question is not None]
        skills = {
            question.primary_kc_id: self.repository.get_skill(question.primary_kc_id)
            for question in active
        }
        selected = select_question(
            active, {kc_id: skill for kc_id, skill in skills.items() if skill is not None},
            self.session_state, utc_now(), selected_question_ids=self.pool_ids, rng=self.rng,
        )
        if selected is not None or len(active) > config.MIN_INTERVENING_QUESTIONS:
            return selected
        # The scheduler remains authoritative for weighting; only its cooldown
        # context is relaxed after no strictly eligible question remains.
        relaxed_state = SessionState(
            self.session_state.session_id,
            question_history=self.session_state.question_history[-1:],
            attempt_counts=dict(self.session_state.attempt_counts),
        )
        return select_question(
            active, {kc_id: skill for kc_id, skill in skills.items() if skill is not None},
            relaxed_state, utc_now(), selected_question_ids=self.pool_ids, rng=self.rng,
        )

    def _complete_when_empty(self) -> None:
        self.finish()
        return None

    @staticmethod
    def _requires_same_session_review(
        correct: bool | None, rating: UnderstandingRating | None, displayed_mastery: float
    ) -> bool:
        return (
            correct is False
            or (correct is True and rating is UnderstandingRating.DIDNT_KNOW_GUESSED)
            or (correct is True and rating is UnderstandingRating.PARTIALLY_KNEW and displayed_mastery < 0.60)
        )

    @staticmethod
    def _review_priority(correct: bool | None, rating: UnderstandingRating | None, error_type: ErrorType | None) -> int:
        if correct is False and error_type is ErrorType.DIDNT_KNOW:
            return 1
        if correct is False and error_type is ErrorType.PARTIAL_SETUP:
            return 2
        if correct is True and rating is UnderstandingRating.DIDNT_KNOW_GUESSED:
            return 3
        if correct is False and error_type is ErrorType.EXECUTION_MISTAKE:
            return 4
        return 5

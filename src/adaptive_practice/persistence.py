"""Minimal SQLite persistence for the single-user adaptive practice engine."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .mastery import process_attempt
from .models import ErrorType, KnowledgeComponent, Question, SessionState, UnderstandingRating


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize caller-provided timestamps so engine and database state agree."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def from_timestamp(value: str | None) -> datetime | None:
    return as_utc(datetime.fromisoformat(value)) if value else None


@dataclass(frozen=True)
class PracticeSession:
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    status: str


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    session_id: str
    question_id: str
    primary_kc_id: str
    started_at: datetime | None
    submitted_at: datetime | None
    response_time_ms: int | None
    selected_answer: str | None
    correct: bool | None
    understanding_rating: UnderstandingRating | None
    error_type: ErrorType | None
    objective_mastery_before: float
    objective_mastery_after: float
    understanding_before: float
    understanding_after: float
    displayed_mastery_before: float
    displayed_mastery_after: float
    question_review_need_before: float
    question_review_need_after: float
    solution_viewed: bool
    skipped: bool
    created_at: datetime


class SQLiteRepository:
    """Explicit SQLite reads and transactional writes for the MVP."""

    def __init__(self, database_path: str | Path) -> None:
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        """Create the schema safely; existing data is never deleted."""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                question_id TEXT PRIMARY KEY, question_text TEXT NOT NULL,
                answer_choices TEXT NOT NULL, correct_answer TEXT, solution TEXT,
                topic TEXT, subtopic TEXT, difficulty INTEGER NOT NULL,
                primary_kc_id TEXT NOT NULL, active INTEGER NOT NULL,
                review_need REAL NOT NULL, last_attempt_at TEXT,
                last_attempt_correct INTEGER, must_review_next_session INTEGER NOT NULL,
                must_review_next_session_set_in_session_id TEXT
            );
            CREATE TABLE IF NOT EXISTS skills (
                kc_id TEXT PRIMARY KEY, objective_mastery REAL NOT NULL,
                understanding_score REAL NOT NULL, displayed_mastery REAL NOT NULL,
                attempts INTEGER NOT NULL, successes INTEGER NOT NULL, failures INTEGER NOT NULL,
                distinct_questions_attempted INTEGER NOT NULL, meta_rating_count INTEGER NOT NULL,
                last_attempt_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                question_id TEXT NOT NULL REFERENCES questions(question_id), primary_kc_id TEXT NOT NULL,
                started_at TEXT, submitted_at TEXT, response_time_ms INTEGER, selected_answer TEXT,
                correct INTEGER, understanding_rating TEXT, error_type TEXT,
                objective_mastery_before REAL NOT NULL, objective_mastery_after REAL NOT NULL,
                understanding_before REAL NOT NULL, understanding_after REAL NOT NULL,
                displayed_mastery_before REAL NOT NULL, displayed_mastery_after REAL NOT NULL,
                question_review_need_before REAL NOT NULL, question_review_need_after REAL NOT NULL,
                solution_viewed INTEGER NOT NULL, skipped INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            -- This relationship, not only a stored count, preserves distinct KC evidence across restarts.
            CREATE TABLE IF NOT EXISTS kc_question_attempts (
                kc_id TEXT NOT NULL REFERENCES skills(kc_id),
                question_id TEXT NOT NULL REFERENCES questions(question_id),
                PRIMARY KEY (kc_id, question_id)
            );
            """
        )
        self.connection.commit()

    def save_question(self, question: Question) -> None:
        with self.connection:
            self._upsert_question(question)

    def get_question(self, question_id: str) -> Question | None:
        row = self.connection.execute("SELECT * FROM questions WHERE question_id = ?", (question_id,)).fetchone()
        return self._question_from_row(row) if row else None

    def list_questions(self) -> list[Question]:
        return [self._question_from_row(row) for row in self.connection.execute("SELECT * FROM questions ORDER BY question_id")]

    def list_questions_for_next_session_review(self) -> list[Question]:
        rows = self.connection.execute("SELECT * FROM questions WHERE must_review_next_session = 1 ORDER BY question_id")
        return [self._question_from_row(row) for row in rows]

    def save_skill(self, skill: KnowledgeComponent) -> None:
        with self.connection:
            self._upsert_skill(skill)

    def get_skill(self, kc_id: str) -> KnowledgeComponent | None:
        row = self.connection.execute("SELECT * FROM skills WHERE kc_id = ?", (kc_id,)).fetchone()
        if row is None:
            return None
        ids = {item[0] for item in self.connection.execute("SELECT question_id FROM kc_question_attempts WHERE kc_id = ?", (kc_id,))}
        skill = KnowledgeComponent(
            kc_id=row["kc_id"], objective_mastery=row["objective_mastery"],
            understanding_score=row["understanding_score"], displayed_mastery=row["displayed_mastery"],
            attempts=row["attempts"], successes=row["successes"], failures=row["failures"],
            distinct_questions_attempted=len(ids) if ids else row["distinct_questions_attempted"],
            meta_rating_count=row["meta_rating_count"], last_attempt_at=from_timestamp(row["last_attempt_at"]),
        )
        skill._attempted_question_ids.update(ids)
        return skill

    def start_session(self, started_at: datetime | None = None) -> PracticeSession:
        session = PracticeSession(str(uuid.uuid4()), as_utc(started_at or utc_now()), None, "ACTIVE")
        with self.connection:
            self.connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (session.session_id, to_timestamp(session.started_at), None, session.status))
        return session

    def get_session(self, session_id: str) -> PracticeSession | None:
        row = self.connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return PracticeSession(row["session_id"], from_timestamp(row["started_at"]), from_timestamp(row["ended_at"]), row["status"]) if row else None

    def finish_session(self, session_id: str, ended_at: datetime | None = None) -> PracticeSession:
        with self.connection:
            updated = self.connection.execute("UPDATE sessions SET ended_at = ?, status = 'COMPLETED' WHERE session_id = ?", (to_timestamp(as_utc(ended_at or utc_now())), session_id))
            if updated.rowcount != 1:
                raise KeyError(f"unknown session: {session_id}")
        session = self.get_session(session_id)
        assert session is not None
        return session

    def list_attempts_for_question(self, question_id: str) -> list[AttemptRecord]:
        return self._list_attempts("question_id", question_id)

    def list_attempts_for_session(self, session_id: str) -> list[AttemptRecord]:
        return self._list_attempts("session_id", session_id)

    def record_attempt(
        self,
        *,
        session_id: str,
        question_id: str,
        correct: bool | None = None,
        understanding_rating: UnderstandingRating | None = None,
        error_type: ErrorType | None = None,
        started_at: datetime | None = None,
        submitted_at: datetime | None = None,
        response_time_ms: int | None = None,
        selected_answer: str | None = None,
        solution_viewed: bool = False,
        skipped: bool = False,
        session_state: SessionState | None = None,
    ) -> AttemptRecord:
        """Apply the engine, then atomically write durable state and history."""
        question = self.get_question(question_id)
        if question is None:
            raise KeyError(f"unknown question: {question_id}")
        skill = self.get_skill(question.primary_kc_id)
        if skill is None:
            raise KeyError(f"unknown skill: {question.primary_kc_id}")
        if self.get_session(session_id) is None:
            raise KeyError(f"unknown session: {session_id}")
        if session_state is not None and session_state.session_id != session_id:
            raise ValueError("session_state does not match session_id")

        submitted = as_utc(submitted_at or utc_now())
        started = as_utc(started_at or submitted)
        before = self._snapshot(question, skill)
        process_attempt(
            question=question, kc=skill, session=session_state or SessionState(session_id),
            correct=correct, understanding_rating=understanding_rating, error_type=error_type,
            attempted_at=submitted, response_time_ms=response_time_ms, skipped=skipped,
        )
        after = self._snapshot(question, skill)
        record = AttemptRecord(
            attempt_id=str(uuid.uuid4()), session_id=session_id, question_id=question.question_id,
            primary_kc_id=question.primary_kc_id, started_at=started, submitted_at=submitted,
            response_time_ms=response_time_ms, selected_answer=selected_answer,
            correct=None if skipped else correct, understanding_rating=understanding_rating,
            error_type=error_type, objective_mastery_before=before["objective_mastery"],
            objective_mastery_after=after["objective_mastery"], understanding_before=before["understanding"],
            understanding_after=after["understanding"], displayed_mastery_before=before["displayed_mastery"],
            displayed_mastery_after=after["displayed_mastery"],
            question_review_need_before=before["review_need"],
            question_review_need_after=after["review_need"], solution_viewed=solution_viewed,
            skipped=skipped, created_at=utc_now(),
        )
        with self.connection:
            self._upsert_question(question)
            self._upsert_skill(skill)
            if not skipped:
                self.connection.execute("INSERT OR IGNORE INTO kc_question_attempts VALUES (?, ?)", (skill.kc_id, question.question_id))
            self._insert_attempt(record)
        return record

    def _upsert_question(self, question: Question) -> None:
        self.connection.execute(
            """
            INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                question_text=excluded.question_text, answer_choices=excluded.answer_choices,
                correct_answer=excluded.correct_answer, solution=excluded.solution, topic=excluded.topic,
                subtopic=excluded.subtopic, difficulty=excluded.difficulty, primary_kc_id=excluded.primary_kc_id,
                active=excluded.active, review_need=excluded.review_need, last_attempt_at=excluded.last_attempt_at,
                last_attempt_correct=excluded.last_attempt_correct,
                must_review_next_session=excluded.must_review_next_session,
                must_review_next_session_set_in_session_id=excluded.must_review_next_session_set_in_session_id
            """,
            (question.question_id, question.question_text, json.dumps(question.answer_choices), question.correct_answer,
             question.solution, question.topic, question.subtopic, question.difficulty, question.primary_kc_id,
             int(question.active), question.review_need, to_timestamp(question.last_attempt_at),
             None if question.last_attempt_correct is None else int(question.last_attempt_correct),
             int(question.must_review_next_session), question.must_review_next_session_set_in_session_id),
        )

    def _upsert_skill(self, skill: KnowledgeComponent) -> None:
        self.connection.execute(
            """
            INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kc_id) DO UPDATE SET
                objective_mastery=excluded.objective_mastery, understanding_score=excluded.understanding_score,
                displayed_mastery=excluded.displayed_mastery, attempts=excluded.attempts,
                successes=excluded.successes, failures=excluded.failures,
                distinct_questions_attempted=excluded.distinct_questions_attempted,
                meta_rating_count=excluded.meta_rating_count, last_attempt_at=excluded.last_attempt_at
            """,
            (skill.kc_id, skill.objective_mastery, skill.understanding_score, skill.displayed_mastery,
             skill.attempts, skill.successes, skill.failures, skill.distinct_questions_attempted,
             skill.meta_rating_count, to_timestamp(skill.last_attempt_at)),
        )

    def _insert_attempt(self, record: AttemptRecord) -> None:
        self.connection.execute(
            "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.attempt_id, record.session_id, record.question_id, record.primary_kc_id,
             to_timestamp(record.started_at), to_timestamp(record.submitted_at), record.response_time_ms,
             record.selected_answer, None if record.correct is None else int(record.correct),
             record.understanding_rating.name if record.understanding_rating else None,
             record.error_type.name if record.error_type else None, record.objective_mastery_before,
             record.objective_mastery_after, record.understanding_before, record.understanding_after,
             record.displayed_mastery_before, record.displayed_mastery_after,
             record.question_review_need_before, record.question_review_need_after,
             int(record.solution_viewed), int(record.skipped), to_timestamp(record.created_at)),
        )

    def _list_attempts(self, field: str, value: str) -> list[AttemptRecord]:
        rows = self.connection.execute(f"SELECT * FROM attempts WHERE {field} = ? ORDER BY created_at, rowid", (value,))
        return [self._attempt_from_row(row) for row in rows]

    @staticmethod
    def _snapshot(question: Question, skill: KnowledgeComponent) -> dict[str, float]:
        return {"objective_mastery": skill.objective_mastery, "understanding": skill.understanding_score,
                "displayed_mastery": skill.displayed_mastery, "review_need": question.review_need}

    @staticmethod
    def _question_from_row(row: sqlite3.Row) -> Question:
        choices = json.loads(row["answer_choices"])
        return Question(question_id=row["question_id"], primary_kc_id=row["primary_kc_id"],
                        choice_count=len(choices), difficulty=row["difficulty"], active=bool(row["active"]),
                        review_need=row["review_need"], last_attempt_at=from_timestamp(row["last_attempt_at"]),
                        last_attempt_correct=None if row["last_attempt_correct"] is None else bool(row["last_attempt_correct"]),
                        must_review_next_session=bool(row["must_review_next_session"]),
                        must_review_next_session_set_in_session_id=row["must_review_next_session_set_in_session_id"],
                        question_text=row["question_text"], answer_choices=choices, correct_answer=row["correct_answer"],
                        solution=row["solution"], topic=row["topic"], subtopic=row["subtopic"])

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=row["attempt_id"], session_id=row["session_id"], question_id=row["question_id"], primary_kc_id=row["primary_kc_id"],
            started_at=from_timestamp(row["started_at"]), submitted_at=from_timestamp(row["submitted_at"]), response_time_ms=row["response_time_ms"],
            selected_answer=row["selected_answer"], correct=None if row["correct"] is None else bool(row["correct"]),
            understanding_rating=UnderstandingRating[row["understanding_rating"]] if row["understanding_rating"] else None,
            error_type=ErrorType[row["error_type"]] if row["error_type"] else None,
            objective_mastery_before=row["objective_mastery_before"], objective_mastery_after=row["objective_mastery_after"],
            understanding_before=row["understanding_before"], understanding_after=row["understanding_after"],
            displayed_mastery_before=row["displayed_mastery_before"], displayed_mastery_after=row["displayed_mastery_after"],
            question_review_need_before=row["question_review_need_before"], question_review_need_after=row["question_review_need_after"],
            solution_viewed=bool(row["solution_viewed"]), skipped=bool(row["skipped"]), created_at=from_timestamp(row["created_at"]),
        )

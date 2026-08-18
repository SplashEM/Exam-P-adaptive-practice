"""Plain local web UI for the MVP practice flow (standard library only)."""

from __future__ import annotations

import html
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from .models import ErrorType, KnowledgeComponent, Question, UnderstandingRating
from .persistence import SQLiteRepository
from .practice import PracticePhase, PracticeSessionController


RATING_LABELS = {
    UnderstandingRating.DIDNT_KNOW_GUESSED: "Didn't know / guessed",
    UnderstandingRating.PARTIALLY_KNEW: "Partially knew",
    UnderstandingRating.KNEW_HOW: "Knew how",
    UnderstandingRating.FULLY_UNDERSTOOD: "Fully understood",
}
ERROR_LABELS = {
    ErrorType.DIDNT_KNOW: "Didn't know how to solve it",
    ErrorType.PARTIAL_SETUP: "Partially understood / setup issue",
    ErrorType.EXECUTION_MISTAKE: "Knew how, execution/calculation mistake",
}


def seed_demo_questions(repository: SQLiteRepository) -> None:
    """Seed small synthetic demo content only when the local bank is empty."""
    if repository.list_questions():
        return
    for kc_id in ("probability_basics", "distributions"):
        repository.save_skill(KnowledgeComponent(kc_id))
    samples = [
        ("demo-1", "TEST Q1 — 2 + 2 = ?", ["A. 3", "B. 4", "C. 5", "D. 6"], "B. 4", "2 + 2 = 4.", "Beta Test", "Arithmetic", 1, "probability_basics"),
        ("demo-2", "TEST Q2 — 3 + 3 = ?", ["A. 5", "B. 6", "C. 7", "D. 8"], "B. 6", "3 + 3 = 6.", "Beta Test", "Arithmetic", 2, "probability_basics"),
        ("demo-3", "TEST Q3 — 4 + 4 = ?", ["A. 6", "B. 7", "C. 8", "D. 9"], "C. 8", "4 + 4 = 8.", "Beta Test", "Arithmetic", 3, "probability_basics"),
        ("demo-4", "TEST Q4 — 5 + 5 = ?", ["A. 8", "B. 9", "C. 10", "D. 11"], "C. 10", "5 + 5 = 10.", "Beta Test", "Arithmetic", 1, "distributions"),
        ("demo-5", "TEST Q5 — 6 + 6 = ?", ["A. 10", "B. 11", "C. 12", "D. 13"], "C. 12", "6 + 6 = 12.", "Beta Test", "Arithmetic", 2, "distributions"),
        ("demo-6", "TEST Q6 — 7 + 7 = ?", ["A. 12", "B. 13", "C. 14", "D. 15"], "C. 14", "7 + 7 = 14.", "Beta Test", "Arithmetic", 3, "distributions"),
    ]
    for item in samples:
        question_id, text, choices, answer, solution, topic, subtopic, difficulty, kc_id = item
        repository.save_question(Question(question_id, kc_id, len(choices), difficulty, question_text=text,
                                          answer_choices=choices, correct_answer=answer, solution=solution,
                                          topic=topic, subtopic=subtopic))


class MinimalPracticeApp:
    """UI-facing adapter; all learning behavior stays in PracticeSessionController."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository
        seed_demo_questions(repository)
        self.demo_reset_enabled = repository.database_path.name == "scheduler_beta.sqlite"
        self.controller: PracticeSessionController | None = None
        self.stats_visible = True
        self.feedback: dict | None = None
        self.pending_answer: dict | None = None
        self.reset_confirmation = False
        self.error: str | None = None
        self.notice: str | None = None

    def start(self, topic: str | None = None, subtopic: str | None = None) -> bool:
        topic = topic.strip() or None if topic else None
        subtopic = subtopic.strip() or None if subtopic else None
        if not self._matching_questions(topic, subtopic):
            self.controller = None
            self.error = "No active questions match the selected topic and subtopic. Choose different filters."
            return False
        self.controller = PracticeSessionController(self.repository, topic=topic, subtopic=subtopic)
        self.feedback = None; self.pending_answer = None; self.error = None
        self.notice = None
        return True

    def home(self) -> None:
        """Discard only completed UI/session-local state; keep persisted study history."""
        if self.controller and self.controller.phase is PracticePhase.COMPLETED:
            self.controller = None
        self.feedback = None; self.pending_answer = None; self.error = None

    def _matching_questions(self, topic: str | None, subtopic: str | None) -> list[Question]:
        return [
            question for question in self.repository.list_questions()
            if question.active
            and (topic is None or question.topic == topic)
            and (subtopic is None or question.subtopic == subtopic)
        ]

    def request_demo_reset(self) -> bool:
        if not self.demo_reset_enabled:
            return False
        self.reset_confirmation = True
        self.error = None
        return True

    def confirm_demo_reset(self) -> bool:
        if not self.demo_reset_enabled or not self.reset_confirmation:
            return False
        self.repository.reset_study_progress()
        self.controller = None
        self.feedback = None
        self.pending_answer = None
        self.reset_confirmation = False
        self.error = "Demo statistics reset."
        return True

    def cancel_demo_reset(self) -> None:
        self.reset_confirmation = False
        self.error = None

    def add_question(self, values: dict[str, str]) -> bool:
        text = values.get("question_text", "").strip(); kc_id = values.get("kc_id", "").strip()
        choices = [values.get(f"choice_{letter}", "").strip() for letter in "ABCDE"]
        choices = [choice for choice in choices if choice]
        selected_choice = values.get("correct_choice", "").strip()
        correct = values.get(f"choice_{selected_choice}", "").strip() if selected_choice else values.get("correct_answer", "").strip()
        solution = values.get("solution", "").strip()
        topic = values.get("topic", "").strip(); subtopic = values.get("subtopic", "").strip()
        try: difficulty = int(values.get("difficulty", ""))
        except ValueError: difficulty = 0
        if not text:
            self.error = "Question text is required."
        elif len(choices) < 2:
            self.error = "Enter at least two answer choices."
        elif selected_choice and selected_choice not in "ABCDE":
            self.error = "Choose the correct answer from the entered choices."
        elif not correct or correct not in choices:
            self.error = "Choose the correct answer from one of the entered choices."
        elif not solution:
            self.error = "A solution or explanation is required."
        elif not topic or not subtopic or not kc_id:
            self.error = "Topic, subtopic, and primary knowledge component are required."
        elif difficulty not in range(1, 6):
            self.error = "Difficulty must be a whole number from 1 to 5."
        else:
            self.error = None
        if self.error:
            return False
        if self.repository.get_skill(kc_id) is None:
            self.repository.save_skill(KnowledgeComponent(kc_id))
        self.repository.save_question(Question(f"manual-{uuid.uuid4()}", kc_id, len(choices), difficulty,
            question_text=text, answer_choices=choices, correct_answer=correct,
            solution=solution, topic=topic, subtopic=subtopic))
        self.notice = "Question saved. It is available in practice and under its topic."
        return True

    def submit_answer(self, selected_answer: str | None, response_time_ms: int) -> bool:
        if self.feedback is not None or self.pending_answer is not None:
            self.error = "This question has already been submitted."
            return False
        question = self.current_question()
        if question is None or not selected_answer:
            self.error = "Choose an answer."
            return False
        self.pending_answer = {"question": question, "selected": selected_answer,
                               "correct": selected_answer == question.correct_answer,
                               "response_time_ms": response_time_ms}
        self.error = None
        return True

    def finalize_rating(self, rating_name: str) -> bool:
        if self.pending_answer is None or self.feedback is not None:
            self.error = "Submit an answer before saving a rating."
            return False
        data = self.pending_answer
        try:
            if data["correct"]:
                rating, error_type = UnderstandingRating[rating_name], None
            else:
                rating, error_type = {
                    "DIDNT_KNOW": (UnderstandingRating.DIDNT_KNOW_GUESSED, ErrorType.DIDNT_KNOW),
                    "PARTIAL_SETUP": (UnderstandingRating.PARTIALLY_KNEW, ErrorType.PARTIAL_SETUP),
                    "EXECUTION_MISTAKE": (UnderstandingRating.KNEW_HOW, ErrorType.EXECUTION_MISTAKE),
                }[rating_name]
        except KeyError:
            self.error = "Choose one of the available rating options."
            return False
        result = self.controller.submit_attempt(data["question"].question_id, selected_answer=data["selected"],
            understanding_rating=rating, error_type=error_type, response_time_ms=data["response_time_ms"], solution_viewed=True)
        self.feedback = {**data, "result": result}; self.pending_answer = None; self.error = None
        return True

    def current_question(self) -> Question | None:
        return self.controller.next_question() if self.controller else None

    def submit(self, selected_answer: str | None, rating_name: str | None, error_name: str | None, response_time_ms: int) -> bool:
        if self.feedback is not None:
            self.error = "This question has already been submitted. Choose Next Question to continue."
            return False
        question = self.current_question()
        if question is None:
            self.error = "No active question is available."
            return False
        if not selected_answer or not rating_name:
            self.error = "Choose both an answer and an understanding rating."
            return False
        correct = selected_answer == question.correct_answer
        if not correct and not error_name:
            self.error = "Choose an error classification for an incorrect answer."
            return False
        try:
            rating = UnderstandingRating[rating_name]
            error_type = ErrorType[error_name] if error_name else None
        except KeyError:
            self.error = "Invalid form value. Choose an available rating and error classification."
            return False
        result = self.controller.submit_attempt(question.question_id, selected_answer=selected_answer,
                                                understanding_rating=rating, error_type=error_type,
                                                response_time_ms=response_time_ms, solution_viewed=True)
        self.feedback = {"question": question, "selected": selected_answer, "result": result}
        self.error = None
        return True

    def next(self) -> None:
        self.feedback = None; self.pending_answer = None; self.error = None

    def finish(self) -> None:
        if self.controller:
            self.controller.finish()
        self.feedback = None

    def summary(self) -> dict[str, float | int]:
        if not self.controller:
            return {"attempts": 0, "correct": 0, "incorrect": 0, "accuracy": 0, "average_time": 0}
        attempts = self.repository.list_attempts_for_session(self.controller.session.session_id)
        submitted = [item for item in attempts if not item.skipped]
        correct = sum(item.correct is True for item in submitted)
        times = [item.response_time_ms for item in submitted if item.response_time_ms is not None]
        return {"attempts": len(submitted), "correct": correct, "incorrect": len(submitted) - correct,
                "accuracy": round(100 * correct / len(submitted), 1) if submitted else 0,
                "average_time": round(sum(times) / len(times)) if times else 0,
                "last_attempted": attempts[-1].submitted_at.isoformat() if attempts and attempts[-1].submitted_at else "Not attempted"}

    def question_stats(self, question_id: str) -> dict[str, float | int | str | list]:
        return self._stats(self.repository.list_attempts_for_question(question_id))

    def overall_stats(self) -> dict[str, float | int | str | list]:
        rows = self.repository.connection.execute("SELECT * FROM attempts ORDER BY created_at, rowid").fetchall()
        return self._stats([self.repository._attempt_from_row(row) for row in rows])

    @staticmethod
    def _stats(attempts):
        submitted = [item for item in attempts if not item.skipped]
        correct = sum(item.correct is True for item in submitted); times = [item.response_time_ms for item in submitted if item.response_time_ms is not None]
        return {"attempts": len(submitted), "correct": correct, "incorrect": len(submitted)-correct,
                "accuracy": round(100*correct/len(submitted), 1) if submitted else 0,
                "average_time": round(sum(times)/len(times)) if times else 0,
                "times": times, "last_attempted": submitted[-1].submitted_at.isoformat() if submitted and submitted[-1].submitted_at else "Not attempted"}


def page(app: MinimalPracticeApp) -> str:
    def esc(value) -> str: return html.escape(str(value))
    header = "<h1>Exam P Adaptive Practice</h1><form method='post' action='/toggle'><button>Toggle statistics</button></form>"
    if app.controller is None:
        topics = sorted({q.topic for q in app.repository.list_questions() if q.topic})
        subtopics = sorted({q.subtopic for q in app.repository.list_questions() if q.subtopic})
        topic_options = "".join(f"<option value='{esc(topic)}'>{esc(topic)}</option>" for topic in topics)
        subtopic_options = "".join(f"<option value='{esc(subtopic)}'>{esc(subtopic)}</option>" for subtopic in subtopics)
        message = f"<p style='color:red'>{esc(app.error)}</p>" if app.error else ""
        notice = f"<p>{esc(app.notice)}</p>" if app.notice else ""
        reset = demo_reset_html(app)
        return f"<html><body>{header}<h2>Start practice</h2>{overall_stats_html(app)}{notice}{message}<form method='post' action='/start'>Topic: <select name='topic'><option value=''>All Questions</option>{topic_options}</select> Subtopic: <select name='subtopic'><option value=''>All Subtopics</option>{subtopic_options}</select> <button>Start Practice</button></form><p><a href='/add-question'>Add Question</a></p>{reset}</body></html>"
    if app.controller.phase is PracticePhase.COMPLETED:
        return f"<html><body>{header}<h2>Session complete</h2>{stats_html(app)}<p><a href='/'>Start another session</a></p></body></html>"
    if app.feedback:
        data = app.feedback; question = data["question"]; result = data["result"]
        verdict = "Correct" if result.correct else "Incorrect"
        stats = stats_html(app, result.displayed_mastery)
        return f"<html><body>{header}<h2>{verdict}</h2><p>Your answer: {esc(data['selected'])}</p><p>Correct answer: {esc(question.correct_answer)}</p>{stats}<p>Same-session review: {result.same_session_review_required}; next-session review: {result.must_review_next_session}</p><details open><summary>View Solution</summary><p>{esc(question.solution or 'No solution supplied.')}</p></details><form method='post' action='/next'><button>Next Question</button></form><form method='post' action='/finish'><button>Finish Session</button></form></body></html>"
    if app.pending_answer:
        data = app.pending_answer; question = data["question"]
        if data["correct"]:
            options = "".join(f"<label><input type='radio' name='rating' value='{r.name}'>{esc(label)}</label><br>" for r, label in RATING_LABELS.items())
        else:
            options = "".join(f"<label><input type='radio' name='rating' value='{e.name}'>{esc(label)}</label><br>" for e, label in ERROR_LABELS.items())
        return f"<html><body>{header}<h2>{'Correct' if data['correct'] else 'Incorrect'}</h2><p>Your answer: {esc(data['selected'])}</p><p>Correct answer: {esc(question.correct_answer)}</p><h3>Rate your understanding</h3><form method='post' action='/rating'>{options}<button>Save Rating</button></form><form method='post' action='/finish'><button>Finish Session</button></form></body></html>"
    question = app.current_question()
    if question is None:
        app.finish()
        return page(app)
    choices = "".join(f"<label><input type='radio' name='answer' value='{esc(choice)}'>{esc(choice)}</label><br>" for choice in question.answer_choices)
    message = f"<p style='color:red'>{esc(app.error)}</p>" if app.error else ""
    skill = app.repository.get_skill(question.primary_kc_id)
    return f"<html><body>{header}<h2>{esc(question.question_text)}</h2><p>{esc(question.topic)} / {esc(question.subtopic)} · Difficulty {question.difficulty}</p>{question_stats_html(app, question.question_id, skill.displayed_mastery if skill else None)}{message}<form method='post' action='/answer'>{choices}<input type='hidden' name='shown_at' value='{time.monotonic()}'><button>Submit Answer</button></form><form method='post' action='/finish'><button>Finish Session</button></form></body></html>"


def add_question_page(app: MinimalPracticeApp) -> str:
    def esc(value): return html.escape(str(value))
    message = f"<p>{esc(app.error)}</p>" if app.error else ""
    choices = "".join(
        f"<p>{letter}: <input name='choice_{letter}'> <label><input type='radio' name='correct_choice' value='{letter}'> Correct answer</label></p>"
        for letter in "ABCDE"
    )
    return f"<html><body><h1>Add Question</h1>{message}<form method='post' action='/save-question'><p>Question text: <textarea name='question_text'></textarea></p>{choices}<p>Solution: <textarea name='solution'></textarea></p><p>Topic: <input name='topic'> Subtopic: <input name='subtopic'></p><p>Difficulty (1–5): <input name='difficulty'></p><p>Primary knowledge component: <input name='kc_id'></p><button>Save Question</button></form><p><a href='/'>Home</a></p></body></html>"


def stats_html(app: MinimalPracticeApp, mastery: float | None = None) -> str:
    if not app.stats_visible:
        return ""
    data = app.summary()
    mastery_text = f" Mastery: {mastery:.0%}." if mastery is not None else ""
    return f"<p>Attempts: {data['attempts']}. Correct: {data['correct']}. Incorrect: {data['incorrect']}. Accuracy: {data['accuracy']}%. Average response time: {data['average_time']} ms. Last attempted: {html.escape(str(data['last_attempted']))}.{mastery_text}</p>"

def question_stats_html(app, question_id, mastery):
    if not app.stats_visible: return ""
    data = app.question_stats(question_id)
    return f"<h3>Question Stats</h3><p>Attempts: {data['attempts']}. Correct: {data['correct']}. Incorrect: {data['incorrect']}. Accuracy: {data['accuracy']}%. Times: {data['times']}. Average response time: {data['average_time']} ms. Skill mastery: {mastery:.0%}. Last attempted: {html.escape(str(data['last_attempted']))}.</p>"

def overall_stats_html(app):
    if not app.stats_visible: return ""
    data = app.overall_stats()
    return f"<h3>Overall Stats</h3><p>Total attempts: {data['attempts']}. Correct: {data['correct']}. Incorrect: {data['incorrect']}. Accuracy: {data['accuracy']}%. Average response time: {data['average_time']} ms.</p>"


def demo_reset_html(app: MinimalPracticeApp) -> str:
    if not app.demo_reset_enabled:
        return ""
    if app.reset_confirmation:
        return ("<h3>Reset Demo Stats</h3><p>This clears all study progress in scheduler_beta.sqlite. "
                "Question content is kept.</p><form method='post' action='/confirm-reset-demo-stats'>"
                "<button>Confirm Reset Demo Stats</button></form>"
                "<form method='post' action='/cancel-reset-demo-stats'><button>Cancel</button></form>")
    return "<form method='post' action='/reset-demo-stats'><button>Reset Demo Stats</button></form>"


def create_server(database_path: str = "adaptive_practice.sqlite", host: str = "127.0.0.1", port: int = 8000) -> HTTPServer:
    repository = SQLiteRepository(database_path); repository.initialize()
    app = MinimalPracticeApp(repository)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in {"/reset-demo-stats", "/confirm-reset-demo-stats", "/cancel-reset-demo-stats"}:
                self._respond("Not Found", status=404); return
            if self.path == "/": app.home()
            self._respond(add_question_page(app) if self.path == "/add-question" else None)
        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            route = self.path
            if route in {"/reset-demo-stats", "/confirm-reset-demo-stats", "/cancel-reset-demo-stats"} and not app.demo_reset_enabled:
                self._respond("Not Found", status=404); return
            if route == "/start": app.start(values.get("topic", [""])[0], values.get("subtopic", [""])[0])
            elif route == "/toggle": app.stats_visible = not app.stats_visible
            elif route == "/next": app.next()
            elif route == "/finish": app.finish()
            elif route == "/answer":
                try:
                    shown = float(values.get("shown_at", [str(time.monotonic())])[0])
                except ValueError:
                    shown = time.monotonic()
                elapsed_ms = max(0, int((time.monotonic() - shown) * 1000))
                app.submit_answer(values.get("answer", [None])[0], elapsed_ms)
            elif route == "/rating": app.finalize_rating(values.get("rating", [None])[0])
            elif route == "/reset-demo-stats": app.request_demo_reset()
            elif route == "/confirm-reset-demo-stats": app.confirm_demo_reset()
            elif route == "/cancel-reset-demo-stats": app.cancel_demo_reset()
            elif route == "/save-question":
                if app.add_question({key: value[0] for key, value in values.items()}):
                    self._redirect("/"); return
                self._respond(add_question_page(app)); return
            self._respond()
        def _respond(self, rendered=None, status=200):
            body = (rendered or page(app)).encode(); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def _redirect(self, location):
            self.send_response(303); self.send_header("Location", location); self.end_headers()
        def log_message(self, *_): pass
    # The repository is intentionally single-threaded for this local MVP.
    return HTTPServer((host, port), Handler)


def run() -> None:
    server = create_server()
    print("Open http://127.0.0.1:8000")
    try: server.serve_forever()
    finally: server.server_close()

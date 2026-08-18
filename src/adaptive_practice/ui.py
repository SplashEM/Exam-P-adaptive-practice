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
        ("demo-1", "A fair coin is tossed once. What is P(heads)?", ["0", "1/2", "1", "2"], "1/2", "A fair coin has two equally likely outcomes.", "Probability", "Basics", 1, "probability_basics"),
        ("demo-2", "A fair die is rolled. What is P(an even result)?", ["1/6", "1/3", "1/2", "2/3"], "1/2", "The even outcomes are 2, 4, and 6: three of six.", "Probability", "Basics", 2, "probability_basics"),
        ("demo-3", "If P(A)=0.4 and P(B)=0.5 for independent events, what is P(A and B)?", ["0.1", "0.2", "0.4", "0.9"], "0.2", "For independent events, multiply: 0.4 × 0.5.", "Probability", "Independence", 3, "probability_basics"),
        ("demo-4", "A Bernoulli random variable with p=0.3 has expected value:", ["0", "0.3", "0.7", "1"], "0.3", "The mean of Bernoulli(p) is p.", "Distributions", "Bernoulli", 1, "distributions"),
        ("demo-5", "For X ~ Binomial(n=2, p=0.5), P(X=2) equals:", ["0.25", "0.5", "0.75", "1"], "0.25", "Both trials must succeed: 0.5².", "Distributions", "Binomial", 2, "distributions"),
        ("demo-6", "For a continuous random variable, P(X equals exactly 3) is:", ["0", "0.25", "0.5", "1"], "0", "A continuous distribution assigns probability zero to a single point.", "Distributions", "Continuous", 3, "distributions"),
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
        self.controller: PracticeSessionController | None = None
        self.stats_visible = True
        self.feedback: dict | None = None
        self.pending_answer: dict | None = None
        self.error: str | None = None

    def start(self, topic: str | None = None, subtopic: str | None = None) -> None:
        self.controller = PracticeSessionController(self.repository, topic=topic or None, subtopic=subtopic or None)
        self.feedback = None; self.error = None

    def add_question(self, values: dict[str, str]) -> bool:
        text = values.get("question_text", "").strip(); kc_id = values.get("kc_id", "").strip()
        choices = [values.get(f"choice_{letter}", "").strip() for letter in "ABCDE"]
        choices = [choice for choice in choices if choice]
        correct = values.get("correct_answer", "").strip()
        try: difficulty = int(values.get("difficulty", ""))
        except ValueError: difficulty = 0
        if not text or not kc_id or not values.get("topic", "").strip() or not values.get("subtopic", "").strip() or len(choices) < 2 or correct not in choices or difficulty not in range(1, 6):
            self.error = "Enter question text, topic, subtopic, KC, difficulty 1–5, two choices, and a matching correct answer."
            return False
        if self.repository.get_skill(kc_id) is None:
            self.repository.save_skill(KnowledgeComponent(kc_id))
        self.repository.save_question(Question(f"manual-{uuid.uuid4()}", kc_id, len(choices), difficulty,
            question_text=text, answer_choices=choices, correct_answer=correct,
            solution=values.get("solution", "").strip(), topic=values["topic"].strip(), subtopic=values["subtopic"].strip()))
        self.error = "Question saved."
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


def page(app: MinimalPracticeApp) -> str:
    def esc(value) -> str: return html.escape(str(value))
    header = "<h1>Exam P Adaptive Practice</h1><form method='post' action='/toggle'><button>Toggle statistics</button></form>"
    if app.controller is None:
        topics = sorted({q.topic for q in app.repository.list_questions() if q.topic})
        options = "".join(f"<option value='{esc(topic)}'>{esc(topic)}</option>" for topic in topics)
        message = f"<p>{esc(app.error)}</p>" if app.error else ""
        return f"<html><body>{header}<h2>Start practice</h2>{message}<form method='post' action='/start'>Topic: <select name='topic'><option value=''>All topics</option>{options}</select> Subtopic: <input name='subtopic'> <button>Start Practice</button></form><p><a href='/add-question'>Add Question</a></p></body></html>"
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
    return f"<html><body>{header}<h2>{esc(question.question_text)}</h2><p>{esc(question.topic)} / {esc(question.subtopic)} · Difficulty {question.difficulty}</p>{stats_html(app, skill.displayed_mastery if skill else None)}{message}<form method='post' action='/answer'>{choices}<input type='hidden' name='shown_at' value='{time.monotonic()}'><button>Submit Answer</button></form><form method='post' action='/finish'><button>Finish Session</button></form></body></html>"


def add_question_page(app: MinimalPracticeApp) -> str:
    def esc(value): return html.escape(str(value))
    message = f"<p>{esc(app.error)}</p>" if app.error else ""
    choices = "".join(f"<p>{letter}: <input name='choice_{letter}'></p>" for letter in "ABCDE")
    correct = "".join(f"<option value='{{{letter}}}'>Use entered {letter}</option>" for letter in "ABCDE")
    return f"<html><body><h1>Add Question</h1>{message}<form method='post' action='/save-question'><p>Question text: <textarea name='question_text'></textarea></p>{choices}<p>Correct answer (enter exact choice text): <input name='correct_answer'></p><p>Solution: <textarea name='solution'></textarea></p><p>Topic: <input name='topic'> Subtopic: <input name='subtopic'></p><p>Difficulty (1–5): <input name='difficulty'></p><p>Primary knowledge component: <input name='kc_id'></p><button>Save Question</button></form><p><a href='/'>Home</a></p></body></html>"


def stats_html(app: MinimalPracticeApp, mastery: float | None = None) -> str:
    if not app.stats_visible:
        return ""
    data = app.summary()
    mastery_text = f" Mastery: {mastery:.0%}." if mastery is not None else ""
    return f"<p>Attempts: {data['attempts']}. Correct: {data['correct']}. Incorrect: {data['incorrect']}. Accuracy: {data['accuracy']}%. Average response time: {data['average_time']} ms. Last attempted: {html.escape(str(data['last_attempted']))}.{mastery_text}</p>"


def create_server(database_path: str = "adaptive_practice.sqlite", host: str = "127.0.0.1", port: int = 8000) -> HTTPServer:
    repository = SQLiteRepository(database_path); repository.initialize()
    app = MinimalPracticeApp(repository)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self._respond(add_question_page(app) if self.path == "/add-question" else None)
        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            route = self.path
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
            elif route == "/save-question":
                app.add_question({key: value[0] for key, value in values.items()})
                self._respond(add_question_page(app)); return
            self._respond()
        def _respond(self, rendered=None):
            body = (rendered or page(app)).encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_): pass
    # The repository is intentionally single-threaded for this local MVP.
    return HTTPServer((host, port), Handler)


def run() -> None:
    server = create_server()
    print("Open http://127.0.0.1:8000")
    try: server.serve_forever()
    finally: server.server_close()

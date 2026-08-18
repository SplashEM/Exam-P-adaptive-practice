from __future__ import annotations

import threading
from urllib.request import urlopen

from adaptive_practice.models import KnowledgeComponent, Question
from adaptive_practice.persistence import SQLiteRepository
from adaptive_practice.ui import MinimalPracticeApp, create_server, page


def app_with_question(tmp_path) -> MinimalPracticeApp:
    repo = SQLiteRepository(tmp_path / "ui.sqlite"); repo.initialize()
    repo.save_skill(KnowledgeComponent("kc"))
    repo.save_question(Question("q1", "kc", 2, 1, question_text="What is 1 + 1?", answer_choices=["1", "2"], correct_answer="2", solution="1 + 1 = 2.", topic="Demo", subtopic="Basics"))
    return MinimalPracticeApp(repo)


def test_app_entry_state_renders_home_screen(tmp_path) -> None:
    app = app_with_question(tmp_path)
    assert "Start Practice" in page(app)
    app.repository.close()


def test_http_get_uses_repository_without_thread_affinity_error(tmp_path) -> None:
    """Serve in the repository's creating thread; client runs separately."""
    server = create_server(str(tmp_path / "server.sqlite"), port=0)
    response: list[bytes] = []
    def request() -> None:
        response.append(urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=2).read())
    client = threading.Thread(target=request)
    client.start(); server.handle_request(); client.join(timeout=2)
    server.server_close()
    assert response and b"Start Practice" in response[0]


def test_start_renders_question_and_requires_answer_and_rating(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert "What is 1 + 1?" in page(app)
    assert app.submit(None, None, None, 10) is False
    assert "Choose both" in app.error
    app.repository.close()


def test_submission_feedback_solution_and_single_attempt(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert app.submit("2", "FULLY_UNDERSTOOD", None, 123) is True
    rendered = page(app)
    assert "Correct" in rendered and "Correct answer: 2" in rendered and "View Solution" in rendered
    attempts = app.repository.list_attempts_for_session(app.controller.session.session_id)
    assert len(attempts) == 1 and attempts[0].response_time_ms == 123
    assert app.submit("2", "FULLY_UNDERSTOOD", None, 123) is False
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 1
    app.repository.close()


def test_error_classification_required_for_wrong_answer(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert app.submit("1", "DIDNT_KNOW_GUESSED", None, 10) is False
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 0
    assert app.submit("1", "DIDNT_KNOW_GUESSED", "DIDNT_KNOW", 10) is True
    assert "Incorrect" in page(app)
    app.repository.close()


def test_stats_toggle_hides_only_display(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start(); app.submit("2", "KNEW_HOW", None, 100)
    assert "Attempts: 1" in page(app) and "Last attempted:" in page(app)
    app.stats_visible = False
    assert "Attempts: 1" not in page(app)
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 1
    app.repository.close()


def test_repeated_or_malformed_submission_does_not_create_attempt(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert app.submit("2", "NOT_A_RATING", None, 10) is False
    assert app.submit("2", "KNEW_HOW", None, 10) is True
    assert app.submit("2", "KNEW_HOW", None, 10) is False
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 1
    app.repository.close()


def test_database_text_is_html_escaped(tmp_path) -> None:
    app = app_with_question(tmp_path)
    question = app.repository.get_question("q1")
    question.question_text = "<script>alert(1)</script>"; question.solution = "<b>unsafe</b>"; question.topic = "<tag>"
    app.repository.save_question(question); app.start()
    rendered = page(app)
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    app.repository.close()


def test_finish_and_empty_pool_are_safe(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start(); app.finish()
    assert "Session complete" in page(app)
    empty_repo = SQLiteRepository(tmp_path / "empty.sqlite"); empty_repo.initialize()
    empty = MinimalPracticeApp(empty_repo)
    # Demo seed supplies a usable bank; a no-match filter reaches completion without a crash.
    empty.start(topic="no-match")
    assert "Session complete" in page(empty)
    app.repository.close(); empty_repo.close()

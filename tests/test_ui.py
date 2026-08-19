from __future__ import annotations

import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from adaptive_practice import config
from adaptive_practice.models import KnowledgeComponent, Question
from adaptive_practice.persistence import SQLiteRepository
from adaptive_practice.ui import MinimalPracticeApp, create_server, page


def app_with_question(tmp_path) -> MinimalPracticeApp:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = SQLiteRepository(tmp_path / "ui.sqlite"); repo.initialize()
    repo.save_skill(KnowledgeComponent("kc"))
    repo.save_question(Question("q1", "kc", 2, 1, question_text="What is 1 + 1?", answer_choices=["1", "2"], correct_answer="2", solution="1 + 1 = 2.", topic="Demo", subtopic="Basics"))
    return MinimalPracticeApp(repo)


def test_app_entry_state_renders_home_screen(tmp_path) -> None:
    app = app_with_question(tmp_path)
    rendered = page(app)
    assert "Start Practice" in rendered and "/static/app.css" in rendered
    assert "Overall Stats" in rendered and "All Topics" in rendered and "All Subtopics" in rendered
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
    rendered = page(app)
    assert "What is 1 + 1?" in rendered and "answer-option" in rendered
    assert "Coverage" in rendered and "Question Stats" in rendered
    assert "Didn't know / guessed" not in rendered
    assert app.submit(None, None, None, 10) is False
    assert "Choose both" in app.error
    app.repository.close()


def test_submission_feedback_solution_and_single_attempt(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert app.submit("2", "FULLY_UNDERSTOOD", None, 123) is True
    rendered = page(app)
    assert "Correct" in rendered and "Correct answer" in rendered and "Solution" in rendered
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
    assert "Session Stats" in page(app)
    app.stats_visible = False
    assert "Session Stats" not in page(app)
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


def test_manual_question_entry_validates_and_persists(tmp_path) -> None:
    app = app_with_question(tmp_path)
    assert app.add_question({"question_text": "Manual?", "choice_A": "yes", "choice_B": "no", "correct_answer": "yes", "solution": "Because.", "topic": "Manual", "subtopic": "Entry", "difficulty": "3", "kc_id": "new-kc"})
    saved = [q for q in app.repository.list_questions() if q.question_text == "Manual?"]
    assert len(saved) == 1 and app.repository.get_skill("new-kc") is not None
    assert not app.add_question({"question_text": "bad", "choice_A": "one", "correct_answer": "missing", "topic": "T", "subtopic": "S", "difficulty": "9", "kc_id": "k"})
    app.repository.close()


def test_manual_question_radio_choice_persists_and_enters_its_topic_pool_after_restart(tmp_path) -> None:
    path = tmp_path / "ui.sqlite"
    app = app_with_question(tmp_path)
    values = {
        "question_text": "Added probability question", "choice_A": "one", "choice_B": "two",
        "correct_choice": "B", "solution": "two is correct", "topic": "Probability Test",
        "subtopic": "Manual Entry", "difficulty": "3", "kc_id": "manual-probability",
    }
    assert app.add_question(values)
    assert "Question saved" in page(app)
    app.start(topic="Probability Test", subtopic="Manual Entry")
    assert app.current_question().question_text == "Added probability question"
    app.repository.close()

    reopened = SQLiteRepository(path); reopened.initialize()
    restored = MinimalPracticeApp(reopened)
    assert "Probability Test" in page(restored)
    assert restored.start(topic="Probability Test") is True
    assert restored.current_question().question_text == "Added probability question"
    reopened.close()


def test_invalid_manual_question_and_no_matching_filter_show_useful_errors(tmp_path) -> None:
    app = app_with_question(tmp_path)
    assert not app.add_question({"question_text": "", "choice_A": "one", "choice_B": "two"})
    assert app.error == "Question text is required."
    assert app.start(topic="Not a topic") is False
    assert "No active questions match" in page(app)
    app.repository.close()


def test_post_answer_rating_flow_creates_exactly_one_attempt(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start()
    assert "Understanding rating" not in page(app)
    assert app.submit_answer("1", 77)
    pending = page(app)
    assert "Incorrect" in pending and "DIDNT_KNOW" in pending and "View Solution" not in pending
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 0
    assert app.finalize_rating("DIDNT_KNOW")
    assert "Solution" in page(app)
    assert len(app.repository.list_attempts_for_session(app.controller.session.session_id)) == 1
    assert not app.finalize_rating("DIDNT_KNOW")
    app.repository.close()


def test_rating_cards_show_only_options_for_the_answer_result(tmp_path) -> None:
    correct = app_with_question(tmp_path / "correct")
    correct.start(); assert correct.submit_answer("2", 20)
    correct_page = page(correct)
    assert "How well did you understand it?" in correct_page
    assert "Fully understood" in correct_page
    assert "Didn't know how to solve it" not in correct_page
    correct.repository.close()

    wrong = app_with_question(tmp_path / "wrong")
    wrong.start(); assert wrong.submit_answer("1", 20)
    wrong_page = page(wrong)
    assert "What went wrong?" in wrong_page
    assert "Partially understood / setup issue" in wrong_page
    assert "Fully understood" not in wrong_page
    wrong.repository.close()


def test_finish_then_start_creates_fresh_session_and_question_stats(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start(); first_id = app.controller.session.session_id
    app.submit_answer("1", 40); app.finalize_rating("DIDNT_KNOW"); app.finish(); app.home()
    assert app.controller is None
    app.start(); second_id = app.controller.session.session_id
    assert second_id != first_id and app.repository.get_session(first_id).status == "COMPLETED"
    rendered = page(app)
    assert "Question Stats" in rendered and "Overall Stats" not in rendered
    assert app.question_stats("q1")["attempts"] == 1
    assert app.overall_stats()["attempts"] == 1
    app.repository.close()


def test_finish_and_empty_pool_are_safe(tmp_path) -> None:
    app = app_with_question(tmp_path); app.start(); app.finish()
    assert "Session complete" in page(app)
    empty_repo = SQLiteRepository(tmp_path / "empty.sqlite"); empty_repo.initialize()
    empty = MinimalPracticeApp(empty_repo)
    # A no-match filter stays on the start page with a useful message.
    assert empty.start(topic="no-match") is False
    assert "No active questions match" in page(empty)
    app.repository.close(); empty_repo.close()


def test_reset_demo_stats_button_is_limited_to_the_named_beta_database(tmp_path) -> None:
    beta_repo = SQLiteRepository(tmp_path / "scheduler_beta.sqlite"); beta_repo.initialize()
    beta = MinimalPracticeApp(beta_repo)
    assert "Reset Demo Stats" in page(beta)
    normal_repo = SQLiteRepository(tmp_path / "adaptive_practice.sqlite"); normal_repo.initialize()
    normal = MinimalPracticeApp(normal_repo)
    assert "Reset Demo Stats" not in page(normal)
    beta_repo.close(); normal_repo.close()


def test_reset_demo_stats_endpoint_rejects_non_demo_database(tmp_path) -> None:
    server = create_server(str(tmp_path / "adaptive_practice.sqlite"), port=0)
    result: list[int] = []

    def request() -> None:
        try:
            urlopen(Request(f"http://127.0.0.1:{server.server_address[1]}/reset-demo-stats", data=b""), timeout=2)
        except HTTPError as error:
            result.append(error.code)

    client = threading.Thread(target=request)
    client.start(); server.handle_request(); client.join(timeout=2)
    server.server_close()
    assert result == [404]


def test_confirmed_demo_reset_preserves_questions_and_restores_fresh_state(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "scheduler_beta.sqlite"); repo.initialize()
    app = MinimalPracticeApp(repo)
    original_questions = [
        (question.question_id, question.question_text, question.answer_choices, question.correct_answer,
         question.solution, question.topic, question.subtopic, question.difficulty, question.primary_kc_id)
        for question in repo.list_questions()
    ]
    assert len(original_questions) == 6
    assert app.request_demo_reset()
    assert "Confirm Reset Demo Stats" in page(app)
    app.cancel_demo_reset()

    app.start()
    question = app.current_question()
    assert question is not None
    assert app.submit_answer(question.answer_choices[0], 321)
    assert app.finalize_rating("DIDNT_KNOW")
    old_session_id = app.controller.session.session_id
    assert repo.list_attempts_for_session(old_session_id)
    assert app.request_demo_reset()
    assert app.reset_confirmation is True
    assert app.confirm_demo_reset()

    assert repo.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert repo.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert repo.get_session(old_session_id) is None
    assert [
        (question.question_id, question.question_text, question.answer_choices, question.correct_answer,
         question.solution, question.topic, question.subtopic, question.difficulty, question.primary_kc_id)
        for question in repo.list_questions()
    ] == original_questions
    for question in repo.list_questions():
        assert question.review_need == 0.50
        assert question.last_attempt_at is None and question.last_attempt_correct is None
        assert question.same_session_review is False and question.must_review_next_session is False
    for kc_id in ("probability_basics", "distributions"):
        skill = repo.get_skill(kc_id)
        assert skill.objective_mastery == config.P0
        assert skill.understanding_score == 0.50
        assert skill.displayed_mastery == config.P0
        assert (skill.attempts, skill.successes, skill.failures, skill.distinct_questions_attempted,
                skill.meta_rating_count, skill.last_attempt_at) == (0, 0, 0, 0, 0, None)

    app.start()
    fresh = app.current_question()
    assert fresh is not None and app.controller.session.session_id != old_session_id
    assert repo.get_session(app.controller.session.session_id).status == "ACTIVE"
    repo.close()

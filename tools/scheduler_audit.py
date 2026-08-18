"""Deterministic scheduler diagnostic; it does not modify application data."""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from adaptive_practice.mastery import mastery_status, process_attempt
from adaptive_practice.models import KnowledgeComponent, Question, SessionState, UnderstandingRating
from adaptive_practice.scheduler import base_question_weight, is_eligible, question_weight, select_question


NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
DRAWS = 100_000
POOL_RUNS = 2_000


def item(
    name: str,
    displayed_mastery: float,
    review_need: float,
    *,
    failed: bool = False,
    days: int = 7,
    mastered: bool = False,
) -> tuple[Question, KnowledgeComponent]:
    question = Question(
        name,
        name,
        5,
        3,
        review_need=review_need,
        last_attempt_at=NOW - timedelta(days=days),
        last_attempt_correct=not failed,
    )
    evidence = {"attempts": 3, "distinct_questions_attempted": 2} if mastered else {}
    return question, KnowledgeComponent(name, displayed_mastery=displayed_mastery, **evidence)


def draw_counts(question_ids: list[str], weights: list[float]) -> Counter[str]:
    return Counter(random.Random(20260818).choices(question_ids, weights=weights, k=DRAWS))


def draw_report(days: list[int]) -> None:
    """Compare original V1 weights with the production suppression policy."""
    items = [
        item("weak", 0.25, 0.85, failed=True, days=days[0]),
        item("developing", 0.50, 0.60, days=days[1]),
        item("competent", 0.70, 0.35, days=days[2]),
        item("strong", 0.88, 0.15, days=days[3]),
        item("mastered", 0.97, 0.05, days=days[4], mastered=True),
    ]
    ids = [question.question_id for question, _ in items]
    before = [base_question_weight(question, kc, NOW) for question, kc in items]
    after = [question_weight(question, kc, NOW) for question, kc in items]
    before_counts = draw_counts(ids, before)
    after_counts = draw_counts(ids, after)
    before_total, after_total = sum(before), sum(after)
    print("draws", "days", days)
    for (question, kc), old, new in zip(items, before, after):
        question_id = question.question_id
        print(
            "  ", question_id, mastery_status(kc),
            "base", round(old, 4), "after", round(new, 4),
            "before-pct", round(old / before_total * 100, 2),
            "before-observed", round(before_counts[question_id] / DRAWS * 100, 2),
            "after-pct", round(new / after_total * 100, 2),
            "after-observed", round(after_counts[question_id] / DRAWS * 100, 2),
        )


def trajectory() -> None:
    """Show one-question evidence trajectory; it stays Strong, not Mastered.

    It intentionally uses one question ID, so the existing two-distinct-question
    evidence requirement prevents the 0.10 Mastered multiplier.
    """
    question = Question("learned", "kc", 5, 3)
    kc = KnowledgeComponent("kc")
    weak_question, weak_kc = item("weak", 0.25, 0.85, failed=True, days=7)
    session = SessionState("audit")
    for attempt_number in range(1, 7):
        process_attempt(
            question=question,
            kc=kc,
            session=session,
            correct=True,
            understanding_rating=UnderstandingRating.FULLY_UNDERSTOOD,
            attempted_at=NOW,
        )
        before = base_question_weight(question, kc, NOW)
        after = question_weight(question, kc, NOW)
        weak_after = question_weight(weak_question, weak_kc, NOW)
        print(
            "trajectory", attempt_number, mastery_status(kc),
            "objective", round(kc.objective_mastery, 4),
            "understanding", round(kc.understanding_score, 4),
            "displayed", round(kc.displayed_mastery, 4),
            "review-need", round(question.review_need, 4),
            "base", round(before, 4), "after", round(after, 4),
            "after-pair-pct", round(after / (after + weak_after) * 100, 2),
        )


def select_before_suppression(
    questions: list[Question], skills: dict[str, KnowledgeComponent], state: SessionState, rng: random.Random
) -> Question | None:
    eligible = [question for question in questions if is_eligible(question, state)]
    if not eligible:
        return None
    return rng.choices(
        eligible,
        weights=[base_question_weight(question, skills[question.primary_kc_id], NOW) for question in eligible],
        k=1,
    )[0]


def small_pool_report(size: int) -> None:
    """Compare 20 ordinary adaptive selections using real cooldown and caps."""
    items = [
        item("mastered", 0.97, 0.05, days=7, mastered=True),
        item("weak", 0.25, 0.85, failed=True, days=7),
    ]
    items.extend(item(f"medium-{index}", 0.60, 0.45, days=7) for index in range(size - 2))
    questions = [question for question, _ in items]
    skills = {skill.kc_id: skill for _, skill in items}

    def run_once(seed: int, *, after: bool) -> list[str]:
        rng = random.Random(seed)
        state = SessionState("audit-small-pool")
        picks = []
        for _ in range(20):
            selected = (
                select_question(questions, skills, state, NOW, rng=rng)
                if after
                else select_before_suppression(questions, skills, state, rng)
            )
            if selected is None:
                break
            picks.append(selected.question_id)
            state.record_appearance(selected.question_id)
            state.record_attempt(selected.question_id)
        return picks

    def summary(after: bool) -> tuple[float, float]:
        totals = [run_once(20260818 + size * 100_000 + run, after=after).count("mastered") for run in range(POOL_RUNS)]
        return sum(totals) / POOL_RUNS, sum(total > 0 for total in totals) / POOL_RUNS * 100

    before_mean, before_once = summary(False)
    after_mean, after_once = summary(True)
    example = run_once(20260818 + size, after=True)
    print(
        "small-pool", size,
        "before-mean", round(before_mean, 3), "before-once-pct", round(before_once, 2),
        "after-mean", round(after_mean, 3), "after-once-pct", round(after_once, 2),
        "after-example", example,
    )


if __name__ == "__main__":
    draw_report([7] * 5)
    draw_report([1, 4, 7, 10, 14])
    trajectory()
    for pool_size in (6, 10, 25, 50):
        small_pool_report(pool_size)

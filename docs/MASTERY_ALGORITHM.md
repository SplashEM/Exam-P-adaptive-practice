# Exam P Adaptive Practice — Mastery Algorithm (V1)

This document is the authoritative V1 learning-engine specification. It documents future implementation requirements only; it does not introduce an implementation, schema, or configuration code.

## Core concepts

Keep these concepts separate:

- **Correctness:** whether a submitted answer is correct.
- **Objective mastery:** the primary knowledge component's Bayesian Knowledge Tracing (BKT) estimate.
- **Self-assessed understanding:** the learner's EWMA-smoothed understanding rating for the primary knowledge component.
- **Question review need:** a per-question scheduling signal derived from the latest attempt severity.

```text
Mastery != Accuracy != Understanding != Review Priority
```

## Knowledge component model

Every question must have `primary_kc_id`. A question may have `secondary_kc_ids[]`, but only the primary KC affects mastery in V1.

Each KC preserves:

```text
objective_mastery
understanding_score
displayed_mastery

attempts
successes
failures

distinct_questions_attempted
meta_rating_count

last_attempt_at
```

## BKT objective mastery

V1 design constants:

```text
P0 = 0.35
PL = 0.05
PS = 0.20
PG = 1 / number_of_answer_choices
DEFAULT_PG = 0.20
```

For an unseen KC, `objective_mastery = 0.35`. Before any attempt, the UI displays **Not enough evidence**, rather than presenting 35% as proven mastery.

Let `M` be current objective mastery. For a correct answer:

```text
posterior =
M * (1 - PS)
/
(
    M * (1 - PS)
    +
    (1 - M) * PG
)

new_objective =
posterior + (1 - posterior) * PL
```

For an incorrect answer:

```text
posterior =
M * PS
/
(
    M * PS
    +
    (1 - M) * (1 - PG)
)

new_objective =
posterior + (1 - posterior) * PL
```

Use the question's number of answer choices for `PG`; use `DEFAULT_PG` if that value is unavailable.

## Understanding model

Map ratings to scores:

```text
DIDNT_KNOW_GUESSED = 0.05
PARTIALLY_KNEW = 0.40
KNEW_HOW = 0.75
FULLY_UNDERSTOOD = 1.00
```

Initial internal understanding is `U0 = 0.50`. Use `UNDERSTANDING_ALPHA = 0.35` and update only when an understanding rating is supplied:

```text
U_new =
U_old
+
0.35 * (rating - U_old)
```

Equivalent form:

```text
U_new =
0.65 * U_old
+
0.35 * rating
```

## Displayed mastery

```text
DisplayedMastery =
0.75 * ObjectiveMastery
+
0.25 * UnderstandingScore
```

```text
OBJECTIVE_WEIGHT = 0.75
UNDERSTANDING_WEIGHT = 0.25
```

If no understanding rating has ever been supplied, `DisplayedMastery = ObjectiveMastery`.

## Mastery bands

```text
0–39%   Weak
40–59%  Developing
60–79%  Competent
80–94%  Strong
95–100% Mastered
```

Assign the actual `MASTERED` label only when all are true:

```text
displayed_mastery >= 0.95
attempts >= 3
distinct_questions_attempted >= 2
```

```text
MASTERY_THRESHOLD = 0.95
MIN_MASTERY_ATTEMPTS = 3
MIN_DISTINCT_MASTERY_QUESTIONS = 2
```

An estimate at or above 95% that fails either evidence minimum remains `Strong`, not `MASTERED`.

## Question review need

Each question maintains `review_need`, initially `0.50`.

Attempt severity is:

```text
Correct + guessed            = 0.80
Correct + partially knew     = 0.55
Correct + knew how           = 0.20
Correct + fully understood   = 0.05

Wrong + didn't know          = 1.00
Wrong + partial/setup        = 0.85
Wrong + execution mistake    = 0.60
Wrong + no classification    = 0.90
```

Update it as:

```text
new_review_need =
0.60 * old_review_need
+
0.40 * severity
```

```text
QUESTION_NEED_OLD_WEIGHT = 0.60
QUESTION_NEED_NEW_WEIGHT = 0.40
```

## Same-session review

Add a question to same-session review when it is incorrect or correct with **Didn't know / guessed**. Also add a correct **Partially knew** question when displayed mastery is below `0.60`.

```text
MIN_INTERVENING_QUESTIONS = 4
MAX_ATTEMPTS_PER_QUESTION_PER_SESSION = 3
```

Wait for at least four intervening questions before the review. Never show a question back-to-back. The maximum of three attempts includes the initial attempt, required review, and adaptive review.

## Next-session review

Every incorrect answer sets `must_review_next_session = true`. A correct answer rated **Didn't know / guessed** also sets it to true.

Do not clear this flag merely because the question is answered correctly later in the same session. In a later session, clear it only when the answer is correct and the understanding rating is `KNEW_HOW` or `FULLY_UNDERSTOOD`.

```text
Weak today
↓
Review later today
↓
Review next session
```

## Adaptive priority and selection

```text
SkillGap = 1 - DisplayedMastery
QuestionNeed = review_need
RecentFailure = 1 if most recent attempt was incorrect else 0
Recency = min(1, days_since_last_attempt / 14)

Priority =
0.45 * SkillGap
+ 0.30 * QuestionNeed
+ 0.15 * RecentFailure
+ 0.10 * Recency

Weight = 0.05 + Priority
```

```text
RECENCY_HORIZON_DAYS = 14
EXPLORATION_FLOOR = 0.05
```

Use weighted random selection, not deterministic selection of the highest-priority question.

### Adaptive eligibility

A question is ineligible when it:

- Appeared in the previous four questions.
- Has already appeared three times in the session.
- Falls outside selected filters.
- Is inactive.

## Difficulty

Store `difficulty = 1–5`:

```text
1 = very easy
2 = easy
3 = medium
4 = hard
5 = very hard
```

Difficulty affects initial ordering only. It does not mathematically alter mastery in V1.

## Response time

Record `response_time_ms` for every answerable attempt. Preserve individual attempt time and calculate average and median time. Response time has zero effect on V1 mastery.

## Skipped questions

For a skipped question:

```text
do not update mastery
do not count success
do not count failure
record skipped = true
```

Skipping is not an incorrect answer.

## Attempt history

Historical attempts are immutable and must never be overwritten. A recommended future attempt record contains:

```text
attempt_id
user_id
session_id
question_id
primary_kc_id

mode

started_at
submitted_at
response_time_ms

selected_answer
correct

understanding_rating
error_type

objective_mastery_before
objective_mastery_after

understanding_before
understanding_after

displayed_mastery_before
displayed_mastery_after

question_review_need_before
question_review_need_after

solution_viewed

created_at
```

This is a recommended record only. Do not create the database schema during Milestone 0.

## Centralized future configuration

The following values must eventually live in centralized configuration so changing a parameter does not require rewriting the algorithm:

```text
P0 = 0.35
PL = 0.05
PS = 0.20
DEFAULT_PG = 0.20

UNDERSTANDING_ALPHA = 0.35

OBJECTIVE_WEIGHT = 0.75
UNDERSTANDING_WEIGHT = 0.25

QUESTION_NEED_OLD_WEIGHT = 0.60
QUESTION_NEED_NEW_WEIGHT = 0.40

MAX_ATTEMPTS_PER_QUESTION_PER_SESSION = 3
MIN_INTERVENING_QUESTIONS = 4

MASTERY_THRESHOLD = 0.95
MIN_MASTERY_ATTEMPTS = 3
MIN_DISTINCT_MASTERY_QUESTIONS = 2

RECENCY_HORIZON_DAYS = 14

EXPLORATION_FLOOR = 0.05
```

This is documentation only. Do not implement configuration code during Milestone 0.

## Required future pseudocode

### `process_attempt(...)`

```text
process_attempt(attempt, question, kc, session):
    preserve immutable before-values on attempt

    if attempt.skipped:
        record skipped attempt
        return without changing mastery, success, failure, review need, or flags

    pg = 1 / question.answer_choice_count, or DEFAULT_PG if unavailable
    M = kc.objective_mastery (or P0 for unseen KC)

    if attempt.correct:
        posterior = M * (1 - PS) / (M * (1 - PS) + (1 - M) * pg)
        kc.successes += 1
    else:
        posterior = M * PS / (M * PS + (1 - M) * (1 - pg))
        kc.failures += 1

    kc.objective_mastery = posterior + (1 - posterior) * PL
    kc.attempts += 1
    update kc.distinct_questions_attempted and kc.last_attempt_at

    if attempt.understanding_rating is supplied:
        rating = score_for(attempt.understanding_rating)
        kc.understanding_score = kc.understanding_score + UNDERSTANDING_ALPHA * (rating - kc.understanding_score)
        kc.meta_rating_count += 1

    if kc.meta_rating_count == 0:
        kc.displayed_mastery = kc.objective_mastery
    else:
        kc.displayed_mastery = OBJECTIVE_WEIGHT * kc.objective_mastery + UNDERSTANDING_WEIGHT * kc.understanding_score

    severity = severity_for(attempt.correct, attempt.understanding_rating, attempt.error_type)
    question.review_need = QUESTION_NEED_OLD_WEIGHT * question.review_need + QUESTION_NEED_NEW_WEIGHT * severity

    if attempt.incorrect or attempt.understanding_rating == DIDNT_KNOW_GUESSED:
        schedule same-session review if eligible
        question.must_review_next_session = true
        question.must_review_next_session_set_in_session_id = session.id
    else if attempt.correct and attempt.understanding_rating == PARTIALLY_KNEW and kc.displayed_mastery < 0.60:
        schedule same-session review if eligible

    if question.must_review_next_session
       and session.id != question.must_review_next_session_set_in_session_id
       and attempt.correct
       and attempt.understanding_rating in {KNEW_HOW, FULLY_UNDERSTOOD}:
        question.must_review_next_session = false

    write immutable attempt record with before- and after-values
```

Here, `attempt.incorrect` means `attempt.correct == false`; `schedule same-session review if eligible` enforces four intervening questions, no back-to-back repeats, and the three-attempt session cap.

### `question_weight(...)`

```text
question_weight(question, primary_kc, now):
    skill_gap = 1 - primary_kc.displayed_mastery
    question_need = question.review_need
    recent_failure = 1 if question.most_recent_attempt was incorrect else 0
    days = days_between(question.last_attempt_at, now)
    recency = min(1, days / RECENCY_HORIZON_DAYS)

    priority =
        0.45 * skill_gap
        + 0.30 * question_need
        + 0.15 * recent_failure
        + 0.10 * recency

    return EXPLORATION_FLOOR + priority
```

Call this only after applying adaptive eligibility. Select among eligible questions using weighted random selection.

## Mandatory future automated tests

When this algorithm is implemented, these tests are mandatory:

1. Correct + Fully understood produces higher displayed mastery than Correct + Guessed.
2. Wrong + Knew how produces higher displayed mastery than Wrong + Didn't know.
3. A wrong answer increases question review need.
4. Correct + Fully understood decreases question review need.
5. A wrong initial-pass question enters both same-session and next-session review.
6. A question cannot appear back-to-back.
7. No question appears more than three times per session.
8. A correct answer increases objective BKT mastery.
9. An incorrect answer should normally decrease objective BKT mastery.
10. Repeated Correct + Fully understood eventually reaches at least 95% displayed mastery.
11. `MASTERED` cannot occur without at least three attempts, at least two distinct questions, and at least 95% displayed mastery.
12. Response time has zero effect on V1 mastery.
13. Skipped questions do not alter mastery.

Tests for future Exam Mode must be documented separately. They do not block the current MVP while Exam Mode remains out of scope.

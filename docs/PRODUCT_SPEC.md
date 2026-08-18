# Exam P Adaptive Practice — Product Specification (MVP V1)

## Purpose

Exam P Adaptive Practice is a single-user personal actuarial practice application. Its central MVP hypothesis is:

> A practice system that remembers both performance and understanding can select better future questions than a system that presents questions only sequentially or randomly.

The MVP supports a reliable adaptive practice loop: the user solves predefined questions, receives feedback and solutions, records understanding, and returns to weak material within and across sessions.

## User outcomes

The user can:

- Solve predefined actuarial multiple-choice questions in the application.
- Encounter every question once, easiest to hardest, on the first pass.
- Receive immediate correct/incorrect feedback and view the solution.
- Record self-assessed understanding and, after an incorrect answer, classify the error.
- Track performance over repeated attempts.
- See weak questions more often and strongly understood questions less often.
- Review weak questions later in the same session and in future sessions.
- Show or hide performance statistics.

## Understanding ratings and feedback

Collect the understanding rating after correctness feedback and before solution viewing:

- Didn't know / guessed
- Partially knew
- Knew how
- Fully understood

For an incorrect answer, also support an error classification:

- Didn't know how to solve it
- Partially understood / setup issue
- Knew how, made an execution or calculation mistake

The rating and error classification are distinct inputs: the rating describes the learner's self-assessment, while the error classification describes the wrong-answer cause.

## Statistics

Track the following for study history and display when statistics are visible:

- Attempts
- Correct
- Incorrect
- Accuracy %
- Time for each attempt
- Average response time
- Understanding / mastery
- Last attempted

Statistics have a visibility toggle. Turning statistics off changes only visibility; the application must continue collecting all data.

When visible, statistics include separate current-question/card statistics and overall aggregate statistics. Finishing a session must allow the user to immediately start a new session while retaining all study history and review requirements.

## Initial pass

For the selected question pool:

```text
Every question appears once
↓
Questions are ordered easiest → hardest
↓
Questions with equal difficulty may be shuffled
↓
No repeats occur during the initial pass
```

After the initial pass, adaptive behavior begins.

## Adaptive behavior

Weak questions appear more frequently; strong questions appear less frequently. Selection retains randomness and must not always select the single weakest question. Significantly weak questions return later in the same session and again in a future session. The same question must not repeat immediately.

## MVP question bank

Automatic importing is not required. MVP validation uses a manually prepared, preloaded question bank of approximately 20–50 questions. The objective is to validate the practice system, not to build the complete Exam P database.

## Explicitly in MVP

- Single-user personal application
- Preloaded/manual question bank
- One-question-at-a-time manual question entry
- Multiple-choice practice
- Question text, answer choices, correct answers, and solutions
- Topic, subtopic, difficulty, and primary knowledge-component metadata
- Easiest → hardest initial pass
- Immediate correctness feedback and solution viewing
- Four-level understanding rating and wrong-answer error classification
- Attempt history; correct/incorrect counts; accuracy; response-time and last-attempt tracking
- Mastery and question review-need tracking
- Same-session review and next-session review
- Adaptive weighted question selection
- Statistics toggle
- Persistent study history

## Explicitly not in MVP

The following are deliberately postponed:

- Automatic TIA importing, screenshot extraction, PDF extraction, and OCR
- AI-generated questions and AI-generated difficulty
- Full Exam Mode and advanced dedicated Review Mode
- Multi-user accounts, social features, leaderboards, and cloud synchronization
- Dedicated mobile application and advanced analytics dashboard
- Deep Knowledge Tracing, IRT, neural mastery models, and multi-KC Bayesian attribution
- Personalized BKT parameter fitting, response-time mastery penalties, and reinforcement-learning scheduling
- Automatic metacognitive calibration and forgetting-curve modeling

Adding any of these requires an explicit scope change.

## Scope freeze and completion

> If a proposed feature is interesting but is not required to make the core adaptive practice loop work, it goes into the backlog.

The MVP is complete when the user can reliably:

```text
start practice
→ answer questions
→ rate understanding
→ receive feedback
→ view solutions
→ generate mastery/review updates
→ encounter adaptive repetition
→ finish the session
→ close/reopen the app
→ retain study history
→ start another session influenced by prior performance
```

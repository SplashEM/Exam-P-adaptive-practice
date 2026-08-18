# Exam P Adaptive Practice

Exam P Adaptive Practice is a single-user personal actuarial practice application. It presents a small, preloaded question bank and uses both answer performance and self-assessed understanding to guide future practice, so weak material is reviewed more often while strong material appears less often.

## Current status

Milestone 5 — Beta Fixes

The project supports persistent multi-session practice through a plain local MVP UI, including one-at-a-time manual question entry and post-answer understanding ratings.

## Run locally

No UI dependency installation is required beyond Python 3. To run tests, install the development dependency with `python3 -m pip install -r requirements-dev.txt`.

Start the local app with:

```bash
PYTHONPATH=src python3 app.py
```

Then open http://127.0.0.1:8000. The app automatically creates a local SQLite database and seeds a small synthetic demo question bank when it is empty. This is a functional MVP UI, intentionally kept plain.

## Frozen MVP specifications

- [Product specification](docs/PRODUCT_SPEC.md)
- [Mastery algorithm](docs/MASTERY_ALGORITHM.md)

## MVP philosophy

This project targets a usable, reliable MVP within one week. Core algorithm correctness, persistent study history, and the end-to-end practice workflow take priority over visual polish and unnecessary architecture.

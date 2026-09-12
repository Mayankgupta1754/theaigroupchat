# Architecture

The AI Group Chat is a **stateless deliberation loop**: four independent models answer the same question in parallel, then a separate judge re-solves the problem and uses the panel only as evidence. There is no database, no session store, and no long-running agent memory. One request in, one JSON result out.

This document explains *why* the system is shaped this way, not just which files exist.

---

## Problem

A single LLM is a bad exam solver for two reasons:

1. **Confidence ≠ correctness.** A model will often emit a fluent wrong answer.
2. **Correlation.** Four models can share the same mistake, so majority vote is not a proof.

The product goal is not “chat with four bots.” It is: **make disagreement visible, then produce one accountable final answer — or refuse.**

---

## High-level shape

```
Browser (vanilla JS + KaTeX)
        │  POST /api/solve  or  POST /api/parse-image
        ▼
Flask (app.py)  ── asyncio ──► OpenRouter (OpenAI-compatible API)
        │                         GPT-4o-mini
        │                         Claude Haiku 4.5
        │                         Gemini 2.5 Flash
        │                         DeepSeek Chat
        ▼
JSON: per-model answers, vote tally, judge, final_answer
```

| Layer | Choice | Why |
| --- | --- | --- |
| UI | One HTML page, no SPA framework | The interaction is a single round-trip; React would add surface area without a state problem to solve |
| Server | Flask + `asyncio` | Flask serves the page and JSON; async is used only for fan-out to models |
| Models | OpenRouter, one API key | One client talks to four providers without four SDKs |
| Persistence | None | A round is self-contained; nothing to sync or migrate |
| Deploy | Vercel Python (`vercel.json`) or Gunicorn locally | Same `app.py` entrypoint |

The OpenAI Python client is pointed at `https://openrouter.ai/api/v1`. Model IDs are provider-prefixed (`openai/gpt-4o-mini`, `anthropic/claude-haiku-4.5`, …).

---

## Request lifecycle

### 1. Ingest

The user pastes text, attaches an image (≤ 4 MB, jpeg/png/webp/gif), or captures from the camera.

- **Text** goes straight to `/api/solve`.
- **Image** is transcribed first (`/api/parse-image` or inline on solve). Vision is **transcription-only**: the extract prompt forbids solving, rewriting, or “fixing” the paper. Shared passages are copied into each question so later stages never need the original page.
- Unreadable bits are marked `[UNREADABLE]` rather than invented.

### 2. Split (batch)

`run_batch` turns one paste into up to **5** self-contained questions.

Order of operations (cheap first):

1. Explicit `<<<Q>>>` separators (what vision emits).
2. Local regex: `1.` / `2)` / `Q1` / `Question 2` / `Problem 3`.
3. Blank-line paragraphs that each look like a question (`?`, “find/calculate/solve…”, or MCQ options). Option-only blocks (`A) … B) …`) are glued back onto the previous stem so a single MCQ is not split.
4. Only if local split returns one blob **and** the text still looks like multiple questions: a JSON splitter model.

This keeps latency and cost down on the common path (numbered papers, blank-line pastes) and avoids a model call for a normal single MCQ.

Each question then runs **`run_panel` concurrently** (`asyncio.gather`). Caps: 16k characters of input, 5 questions per request.

### 3. Panel

Four solver calls fire at once. `temperature` is **0** — this is a solver, not a brainstorm.

| Mode | Solver output | Timeouts | Max tokens |
| --- | --- | --- | --- |
| **Answer** | `ANSWER:` + one sentence (≤ 18 words) | panel 25s / judge 20s | 120 |
| **Solve** | `ANSWER:` + `SOLUTION:` with LaTeX | panel 50s / judge 45s | 1800 / 2200 |

A hung provider cannot stall the round: each call is wrapped in `asyncio.wait_for`. Failures become `{ status: "error" }` cards, not HTTP 500s for the whole batch.

Allowed solver statuses: `success`, `skipped` (model returned SKIP / unsure), `error` (timeout, parse failure, or provider down).

### 4. Canonicalize

Raw model text is messy (`B`, `b)`, `A, C`, `Final answer: 1.5`). `canonicalize_answer` normalizes:

- MCQ letters to a sorted unique string (`A,C` and `C, A` both become `AC`)
- SKIP-family tokens (`UNKNOWN`, `IDK`, `N/A`, …) to `SKIP`
- Short numerical / free-text answers, trimmed

Without this step, consensus counting would treat equivalent answers as different votes.

### 5. Consensus (display, not truth)

Votes are counted among **successful** panel answers only. The UI shows `agree / total` so disagreement is visible. Consensus is **not** the final answer.

### 6. Judge

The judge receives the original question plus the panel’s answers and reasons. Its system prompt requires it to **solve independently first** and treat candidates as evidence, not majority as authority.

Outcomes:

| Judge result | `final_answer` |
| --- | --- |
| Parsed answer | Judge’s answer |
| SKIP | None (honest refuse) |
| Judge error/skip **and** ≥ 3 models agree | Majority answer, `judge.status = "fallback"` |
| No successful panel answers | Judge `unavailable`, no final answer |

Fallback is labelled so the UI does not pretend the judge succeeded.

### 7. Render

The browser plays cards in arrival-time order, then fills the consensus box. Multiple items become **Q1 / Q2 / …** tabs. Math is rendered with KaTeX (`renderToString`, HTML output only) so `$...$`, `\[...\]`, and bare `\frac` survive.

---

## Endpoints

| Method | Path | Role |
| --- | --- | --- |
| `GET` | `/` | UI |
| `GET` | `/health` | Liveness (`{ "ok": true }`) |
| `POST` | `/api/parse-image` | Multipart image → transcribed text (editable before solve) |
| `POST` | `/api/solve` | JSON `{ question, mode }` or multipart with optional image → panel result |

`mode` is `answer` (default) or `solve`. Flask is sync at the HTTP boundary (`asyncio.run(...)`) because a typical WSGI worker is sync; concurrency is **inside** a request, across model HTTP calls, not across requests.

---

## Failure and honesty

The system prefers a visible failure over a fabricated answer.

| Condition | Behaviour |
| --- | --- |
| Missing `OPENROUTER_API_KEY` | 500 with a clear error, no fake panel |
| Image too large / wrong type | 400 / 413 |
| Vision cannot read a question | 400, user can edit or retry |
| One model times out | That card is `error`; others still judge |
| All models fail | No final answer |
| Incomplete / contradictory question | Models and judge may `SKIP` |
| Batch longer than 5 questions | First 5, `truncated: true` |

---

## Why not the obvious alternatives

**Majority vote as the product.** Cheap, and wrong when the panel shares a misconception. The judge exists specifically to beat a confident majority.

**One “best” model.** Removes the point of the UI: seeing *where* models diverge is part of the result.

**A real chat / agent with tools.** Out of scope. This is a bounded exam-style solver with timeouts and a structured contract (`ANSWER:`), not an open-ended assistant.

**Per-provider SDKs.** OpenRouter keeps the client and key count at one. Swapping a panel member is a dict edit in `MODELS`.

**Queue / workers / Redis.** A round is seconds, not minutes. Extra infrastructure would not change the user-visible design.

---

## Source map

```
app.py                 HTTP, split, panel, judge, vision, parsing
templates/index.html   Shell: composer, session, camera
static/script.js       Upload, camera, Q-tabs, KaTeX, round playback
static/style.css       Layout
```

Hot paths in `app.py`: `split_questions` → `run_batch` → `run_panel` → `call_model` → `parse_model_output` / `canonicalize_answer`.

---

## Constraints (intentional)

These are product limits, not leftovers:

- 4 panel models + 1 judge
- ≤ 5 questions per request
- ≤ 4 MB images
- Hard per-call timeouts
- Temperature 0
- No persistence of questions or answers on the server

They keep a round snappy, costs predictable, and the architecture small enough to read in one sitting.

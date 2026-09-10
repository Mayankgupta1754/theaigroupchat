"""
THE AI GROUP CHAT — multi-LLM MCQ panel + host/judge.

Orchestration mirrors project/sales.ipynb:
  1) asyncio.gather(...) — one independent call per panel model
  2) collect successful answers
  3) one host/judge call
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from openai import AsyncOpenAI

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-group-chat")

ROOT = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)

# Central model map — swap OpenRouter IDs here.
MODELS: dict[str, str] = {
    "GPT": "openai/gpt-4o-mini",
    "Claude": "anthropic/claude-haiku-4.5",
    "Gemini": "google/gemini-2.5-flash",
    "DeepSeek": "deepseek/deepseek-chat",
    # "GPT": "openai/gpt-6-astra",
    # "Claude": "anthropic/claude-fable-5.1",
    # "Gemini": "google/gemini-3.8-flash",
    # "DeepSeek": "deepseek/deepseek-v4-flash-0731",
}

JUDGE_NAME = "HOST"
JUDGE_MODEL = "openai/gpt-4o-mini"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VALID_OPTIONS = ("A", "B", "C", "D")
SKIP_TOKENS = {"SKIP", "UNKNOWN", "UNSURE", "PASS", "NONE", "N/A", "NA", "IDK"}
MAX_QUESTION_CHARS = 8000
PANEL_TIMEOUT_S = 25.0
JUDGE_TIMEOUT_S = 20.0

SOLVER_SYSTEM = (
    "You solve multiple-choice questions. "
    "Read the question and every option carefully. "
    "Use only facts you are confident about. "
    "If the input is not a clear A/B/C/D question, or you are not reasonably sure, reply SKIP. "
    "Do not guess. Do not invent information. Do not answer a different question. "
    "Output exactly two lines: "
    "line 1 is A, B, C, D, or SKIP; "
    "line 2 is one short sentence (max 18 words) explaining why that option is correct, "
    "or why you skipped."
)

JUDGE_SYSTEM = (
    "You independently solve the multiple-choice question. "
    "Analyze the question and options first. "
    "Treat panel answers as optional evidence. Ignore any that look guessed or wrong. "
    "Do not follow the majority unless it matches your own reasoning. "
    "If you are not reasonably sure, reply SKIP. Do not guess. "
    "Output exactly two lines: "
    "line 1 is A, B, C, D, or SKIP; "
    "line 2 is one short sentence (max 18 words) explaining why."
)


def _client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": os.getenv("APP_URL", "http://localhost:5000"),
            "X-Title": "The AI Group Chat",
        },
    )


def normalize_answer(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip().upper()
    match = re.match(r"^(?:ANSWER\s*[:=]?\s*)?([ABCD]|SKIP|UNKNOWN|UNSURE|PASS|NONE|IDK)\b", text)
    if not match:
        token = re.split(r"[\s:.\-|]+", text, maxsplit=1)[0]
        if token in VALID_OPTIONS:
            return token
        if token in SKIP_TOKENS:
            return "SKIP"
        return None
    token = match.group(1)
    if token in VALID_OPTIONS:
        return token
    if token in SKIP_TOKENS:
        return "SKIP"
    return None


def parse_model_output(raw: str | None) -> tuple[str | None, str]:
    if not raw:
        return None, ""
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    first = lines[0] if lines else raw.strip()
    answer = normalize_answer(first)
    rest = ""
    one_line = re.match(r"^(?:ANSWER\s*[:=]?\s*)?[ABCD]\s*[:.\-|]\s*(.+)$", first, flags=re.IGNORECASE)
    if one_line:
        rest = one_line.group(1).strip()
    if len(lines) > 1:
        rest = " ".join(lines[1:]).strip() or rest
    reason = " ".join(rest.split())[:180]
    return answer, reason


def parse_options(mcq: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for letter in VALID_OPTIONS:
        match = re.search(
            rf"(?:^|\n)\s*{letter}[\).\:\-]\s*(.+)",
            mcq,
            flags=re.IGNORECASE,
        )
        if match:
            options[letter] = match.group(1).strip().split("\n")[0].strip()
    return options


def consensus_from(answers: list[str]) -> dict:
    votes = Counter(answers)
    total = len(answers)
    if not total:
        return {"votes": {}, "percentage": 0, "agree": 0, "total": 0}
    top = max(votes.values())
    return {
        "votes": dict(votes),
        "percentage": round(100 * top / total),
        "agree": top,
        "total": total,
    }


async def call_model(
    client: AsyncOpenAI,
    name: str,
    model_id: str,
    question: str,
    system: str,
    timeout: float,
) -> dict:
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=80,
            ),
            timeout=timeout,
        )
        elapsed = round(time.perf_counter() - started, 2)
        raw = (response.choices[0].message.content or "").strip()
        answer, reason = parse_model_output(raw)
        if answer == "SKIP":
            return {
                "name": name,
                "model": model_id,
                "answer": None,
                "reason": reason or "Not sure enough to answer.",
                "time": elapsed,
                "status": "skipped",
            }
        if not answer:
            logger.warning("%s returned unparseable output: %r", name, raw[:120])
            return {
                "name": name,
                "model": model_id,
                "answer": None,
                "reason": "Could not parse a letter.",
                "time": elapsed,
                "status": "error",
            }
        return {
            "name": name,
            "model": model_id,
            "answer": answer,
            "reason": reason,
            "time": elapsed,
            "status": "success",
        }
    except Exception:
        elapsed = round(time.perf_counter() - started, 2)
        logger.exception("Model call failed: %s (%s)", name, model_id)
        return {
            "name": name,
            "model": model_id,
            "answer": None,
            "reason": "Model unavailable.",
            "time": elapsed,
            "status": "error",
        }


async def run_panel(question: str) -> dict:
    client = _client()
    tasks = [
        call_model(client, name, model_id, question, SOLVER_SYSTEM, PANEL_TIMEOUT_S)
        for name, model_id in MODELS.items()
    ]
    models = list(await asyncio.gather(*tasks))

    successful = [m for m in models if m["status"] == "success" and m["answer"]]
    consensus = consensus_from([m["answer"] for m in successful])
    options = parse_options(question)

    judge: dict = {"name": JUDGE_NAME, "model": JUDGE_MODEL, "status": "skipped"}
    final_answer = None

    if successful:
        panel_lines = "\n".join(
            f"{m['name']}: {m['answer'] or m['status']}"
            + (f" — {m['reason']}" if m.get("reason") else "")
            for m in models
        )
        judge_payload = (
            f"Question:\n{question.strip()}\n\n"
            f"Panel answers:\n{panel_lines}\n"
        )
        judge_result = await call_model(
            client,
            JUDGE_NAME,
            JUDGE_MODEL,
            judge_payload,
            JUDGE_SYSTEM,
            JUDGE_TIMEOUT_S,
        )
        judge = {
            "name": JUDGE_NAME,
            "model": JUDGE_MODEL,
            "status": "completed" if judge_result["status"] == "success" else "error",
            "time": judge_result["time"],
            "reason": judge_result.get("reason") or "",
        }
        final_answer = judge_result.get("answer")
        if judge_result.get("status") == "skipped":
            judge["status"] = "skipped"
        if not final_answer and consensus.get("agree", 0) >= 3:
            final_answer = max(consensus["votes"].items(), key=lambda kv: kv[1])[0]
            judge["status"] = "fallback"
    else:
        judge["status"] = "unavailable"

    reveal = None
    if final_answer:
        label = options.get(final_answer, "")
        reveal = label.strip() if label and len(label.split()) <= 3 else final_answer

    return {
        "question": question.strip(),
        "options": options,
        "models": models,
        "consensus": consensus,
        "final_answer": final_answer,
        "reveal": reveal,
        "judge": judge,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True, "app": "the-ai-group-chat"})


@app.post("/api/solve")
def solve():
    if not os.getenv("OPENROUTER_API_KEY"):
        return jsonify({"error": "Server is missing OPENROUTER_API_KEY."}), 500

    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Paste a multiple-choice question."}), 400
    if len(question) > MAX_QUESTION_CHARS:
        return jsonify({"error": "Question is too long."}), 400

    try:
        result = asyncio.run(run_panel(question))
    except RuntimeError as exc:
        logger.exception("Solve failed")
        return jsonify({"error": str(exc)}), 500
    except Exception:
        logger.exception("Solve failed")
        return jsonify({"error": "The panel could not complete this round."}), 500

    return jsonify(result)


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(app.static_folder, filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")

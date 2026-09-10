"""
THE AI GROUP CHAT — multi-LLM panel + host/judge.

Orchestration:
  1) split pasted/photo text into individual questions
  2) asyncio.gather(...) — one independent call per panel model
  3) one host/judge call per question
"""

from __future__ import annotations

import asyncio
import base64
import json
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
MAX_IMAGE_BYTES = 4 * 1024 * 1024

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES + 64_000

# Central model map — swap OpenRouter IDs here.
MODELS: dict[str, str] = {
    "GPT": "openai/gpt-4o-mini",
    "Claude": "anthropic/claude-haiku-4.5",
    "Gemini": "google/gemini-2.5-flash",
    "DeepSeek": "deepseek/deepseek-chat",
}

JUDGE_NAME = "HOST"
JUDGE_MODEL = "openai/gpt-4o-mini"
JUDGE_SOLVE_MODEL = "google/gemini-2.5-flash"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VALID_OPTIONS = ("A", "B", "C", "D")
SKIP_TOKENS = {"SKIP", "UNKNOWN", "UNSURE", "PASS", "NONE", "N/A", "NA", "IDK"}
MAX_QUESTION_CHARS = 16000
MAX_QUESTIONS = 5
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}
PANEL_TIMEOUT_S = 25.0
JUDGE_TIMEOUT_S = 20.0
SOLVE_PANEL_TIMEOUT_S = 50.0
SOLVE_JUDGE_TIMEOUT_S = 45.0
EXTRACT_TIMEOUT_S = 40.0
SPLIT_TIMEOUT_S = 25.0
VISION_MODEL = "openai/gpt-4o-mini"

ANSWER_SOLVER_SYSTEM = (
    "You solve JEE Main/Advanced and similar exam questions: "
    "single-correct MCQ, multi-correct, numerical, integer, or one-line answers. "
    "Do the work carefully. Numerical and integer answers are valid — do not skip them. "
    "If the input is not a question, or you cannot solve it, reply SKIP. "
    "Do not guess wildly. Do not answer a different question. "
    "Output exactly two lines: "
    "line 1 is ANSWER: followed by A, B, C, D, a letter set like A,C, a number, or SKIP; "
    "line 2 is one short sentence (max 18 words) explaining why."
)

SOLVE_SOLVER_SYSTEM = (
    "You are an expert JEE Advanced tutor in Physics, Chemistry, and Mathematics. "
    "Solve the given question completely. It may be MCQ, multi-correct, numerical, or integer type. "
    "Show the method a serious student needs: known data, principle/formula, algebra, "
    "substitution, and the boxed final answer with units if relevant. "
    "Write every formula as LaTeX inside $...$ or $$...$$, for example $\\frac{a}{b}$ and $v = u + at$. "
    "Do not skip because it is hard or because it is not A/B/C/D. "
    "If it is truly unsolvable from the given data, reply SKIP. "
    "Output in this format:\n"
    "ANSWER: <final answer only>\n"
    "SOLUTION:\n"
    "<clear step-by-step solution>"
)

ANSWER_JUDGE_SYSTEM = (
    "You independently solve the exam question (MCQ, numerical, or integer). "
    "Treat panel answers as optional evidence. Ignore guesses. "
    "Do not follow the majority unless it matches your own reasoning. "
    "Numerical answers are valid. If you are not reasonably sure, reply SKIP. "
    "Output exactly two lines: "
    "line 1 is ANSWER: followed by the final answer or SKIP; "
    "line 2 is one short sentence (max 18 words) explaining why."
)

SOLVE_JUDGE_SYSTEM = (
    "You independently solve the exam question as a JEE Advanced tutor. "
    "Analyze first. Treat panel answers as optional evidence and ignore wrong ones. "
    "Write a complete, correct method: setup, equations, steps, and final answer. "
    "Write every formula as LaTeX inside $...$ or $$...$$, for example $\\frac{a}{b}$. "
    "If the panel disagrees, say which approach is right and why. "
    "Output in this format:\n"
    "ANSWER: <final answer only>\n"
    "SOLUTION:\n"
    "<detailed step-by-step solution>"
)

EXTRACT_SYSTEM = (
    "You transcribe exam questions from photos, including JEE numericals and multi-question pages. "
    "Copy every question stem, given data, and options exactly. "
    "Keep numbers, units, exponents, and punctuation. "
    "If there are several questions, separate them with a line that contains only <<<Q>>>. "
    "If a common passage has more than one question, copy the passage into each block. "
    "Do not answer. Do not add commentary. "
    "If the image is not a question, output SKIP."
)

SPLIT_SYSTEM = (
    "Split exam paper text into individual questions. "
    "Return ONLY JSON: {\"questions\": [\"...\", \"...\"]}. "
    "Each string is one full question including options and data. "
    "If a passage has multiple questions, copy the passage into each. "
    "Keep (a)(b)(c) sub-parts together when they belong to one question. "
    "If there is only one question, return a one-element array. Do not solve."
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


def canonicalize_answer(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"^[\s>*#-]+", "", raw.strip())
    text = re.sub(r"^(?:final\s+)?answer\s*[:=]\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" .")
    if not text:
        return None
    upper = text.upper()
    token = re.split(r"[\s:.\-|]+", upper, maxsplit=1)[0]
    if token in SKIP_TOKENS:
        return "SKIP"
    letters = re.fullmatch(r"[ABCD](?:\s*,\s*[ABCD]){0,3}", upper)
    if letters:
        found = "".join(sorted(set(re.findall(r"[ABCD]", upper))))
        return found
    compact = re.fullmatch(r"[ABCD]{1,4}", upper.replace(" ", "").replace(",", ""))
    if compact and all(ch in VALID_OPTIONS for ch in compact.group(0)):
        return "".join(sorted(set(compact.group(0))))
    if len(text) > 80:
        text = text[:80].rstrip()
    return re.sub(r"\s+", " ", text)


def parse_model_output(raw: str | None, mode: str = "answer") -> tuple[str | None, str]:
    if not raw:
        return None, ""
    text = raw.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    answer_line = lines[0] if lines else text
    match = re.search(r"(?im)^(?:final\s+)?answer\s*[:=]\s*(.+)$", text)
    if match:
        answer_line = match.group(1).strip()
    answer = canonicalize_answer(answer_line)

    solution = text
    solution = re.sub(r"(?im)^(?:final\s+)?answer\s*[:=]\s*.+$", "", solution, count=1)
    solution = re.sub(r"(?im)^solution\s*[:=]\s*", "", solution.strip(), count=1)
    solution = solution.strip()
    if not solution and len(lines) > 1:
        solution = " ".join(lines[1:]).strip()
    if mode == "answer":
        solution = " ".join(solution.split())[:180]
    else:
        solution = solution[:8000]
    return answer, solution


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


def split_questions_local(text: str) -> list[str]:
    blob = text.strip()
    if not blob:
        return []
    if "<<<Q>>>" in blob:
        return [part.strip() for part in blob.split("<<<Q>>>") if part.strip()]
    pattern = re.compile(
        r"(?im)^(?:(?:question|q)\s*\.?\s*\d+\s*[\).:]?|\d+\s*[\).])\s+"
    )
    matches = list(pattern.finditer(blob))
    if len(matches) >= 2:
        parts = []
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
            chunk = blob[match.start():end].strip()
            if len(chunk) > 12:
                parts.append(chunk)
        if len(parts) >= 2:
            return parts
    return [blob]


def looks_like_multiple(text: str) -> bool:
    option_blocks = len(re.findall(r"(?im)(?:^|\n)\s*A[\).\:-]\s+", text))
    numbered = len(re.findall(r"(?im)^(?:(?:question|q)\s*\.?\s*\d+|\d+\s*[\).])\s+", text))
    return option_blocks >= 2 or numbered >= 2


async def split_questions(client: AsyncOpenAI, text: str) -> list[str]:
    local = split_questions_local(text)
    if len(local) >= 2:
        return local[:MAX_QUESTIONS]
    if not looks_like_multiple(text):
        return local[:1] or [text.strip()]
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {"role": "system", "content": SPLIT_SYSTEM},
                    {"role": "user", "content": text.strip()[:MAX_QUESTION_CHARS]},
                ],
                temperature=0,
                max_tokens=2000,
            ),
            timeout=SPLIT_TIMEOUT_S,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        data = json.loads(raw)
        items = [str(item).strip() for item in data.get("questions", []) if str(item).strip()]
        if items:
            return items[:MAX_QUESTIONS]
    except Exception:
        logger.exception("Question split failed; using local split")
    return local[:MAX_QUESTIONS] or [text.strip()]


async def call_model(
    client: AsyncOpenAI,
    name: str,
    model_id: str,
    question: str,
    system: str,
    timeout: float,
    mode: str = "answer",
    max_tokens: int = 80,
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
                max_tokens=max_tokens,
            ),
            timeout=timeout,
        )
        elapsed = round(time.perf_counter() - started, 2)
        raw = (response.choices[0].message.content or "").strip()
        answer, reason = parse_model_output(raw, mode)
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
                "reason": "Could not parse an answer.",
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


def _read_image_upload():
    file = request.files.get("image")
    if file is None or not file.filename:
        return None, None
    mime = (file.mimetype or "").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Please upload a JPG, PNG, WEBP, or GIF image.")
    data = file.read()
    if not data:
        raise ValueError("The uploaded image was empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large. Keep it under 4 MB.")
    if mime == "image/jpg":
        mime = "image/jpeg"
    return data, mime


async def extract_question_from_image(client: AsyncOpenAI, image_bytes: bytes, mime: str) -> str:
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Transcribe every exam question from this image.",
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0,
            max_tokens=2200,
        ),
        timeout=EXTRACT_TIMEOUT_S,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text or text.upper().startswith("SKIP"):
        raise ValueError("Could not read a question from that image.")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError("The transcribed question is too long.")
    return text


async def run_panel(question: str, mode: str = "answer") -> dict:
    client = _client()
    solve = mode == "solve"
    solver_system = SOLVE_SOLVER_SYSTEM if solve else ANSWER_SOLVER_SYSTEM
    judge_system = SOLVE_JUDGE_SYSTEM if solve else ANSWER_JUDGE_SYSTEM
    panel_timeout = SOLVE_PANEL_TIMEOUT_S if solve else PANEL_TIMEOUT_S
    judge_timeout = SOLVE_JUDGE_TIMEOUT_S if solve else JUDGE_TIMEOUT_S
    panel_tokens = 1800 if solve else 120
    judge_tokens = 2200 if solve else 120
    judge_model = JUDGE_SOLVE_MODEL if solve else JUDGE_MODEL

    tasks = [
        call_model(
            client,
            name,
            model_id,
            question,
            solver_system,
            panel_timeout,
            mode=mode,
            max_tokens=panel_tokens,
        )
        for name, model_id in MODELS.items()
    ]
    models = list(await asyncio.gather(*tasks))

    successful = [m for m in models if m["status"] == "success" and m["answer"]]
    consensus = consensus_from([m["answer"] for m in successful])
    options = parse_options(question)

    judge: dict = {"name": JUDGE_NAME, "model": judge_model, "status": "skipped", "reason": ""}
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
            judge_model,
            judge_payload,
            judge_system,
            judge_timeout,
            mode=mode,
            max_tokens=judge_tokens,
        )
        judge = {
            "name": JUDGE_NAME,
            "model": judge_model,
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
        "mode": mode,
    }


async def run_batch(raw_text: str, mode: str) -> dict:
    client = _client()
    questions = await split_questions(client, raw_text)
    truncated = len(questions) > MAX_QUESTIONS
    questions = questions[:MAX_QUESTIONS]
    results = await asyncio.gather(*[run_panel(question, mode) for question in questions])
    items = []
    for index, result in enumerate(results, start=1):
        result["index"] = index
        items.append(result)
    return {
        "mode": mode,
        "count": len(items),
        "truncated": truncated,
        "items": items,
        "question": items[0]["question"] if items else raw_text.strip(),
        "options": items[0]["options"] if items else {},
        "models": items[0]["models"] if items else [],
        "consensus": items[0]["consensus"] if items else {},
        "final_answer": items[0]["final_answer"] if items else None,
        "reveal": items[0]["reveal"] if items else None,
        "judge": items[0]["judge"] if items else {},
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True, "app": "the-ai-group-chat"})


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Image is too large. Keep it under 4 MB."}), 413


@app.post("/api/parse-image")
def parse_image():
    if not os.getenv("OPENROUTER_API_KEY"):
        return jsonify({"error": "Server is missing OPENROUTER_API_KEY."}), 500
    try:
        image_bytes, mime = _read_image_upload()
        if not image_bytes:
            return jsonify({"error": "Upload an image of the question."}), 400
        question = asyncio.run(extract_question_from_image(_client(), image_bytes, mime))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        logger.exception("Image parse failed")
        return jsonify({"error": str(exc)}), 500
    except Exception:
        logger.exception("Image parse failed")
        return jsonify({"error": "Could not read that image. Try a clearer photo."}), 500
    return jsonify({"question": question})


def _read_mode(payload: dict) -> str:
    mode = str(payload.get("mode") or "answer").strip().lower()
    return "solve" if mode == "solve" else "answer"


@app.post("/api/solve")
def solve():
    if not os.getenv("OPENROUTER_API_KEY"):
        return jsonify({"error": "Server is missing OPENROUTER_API_KEY."}), 500

    question = ""
    mode = "answer"
    try:
        if request.content_type and "multipart/form-data" in request.content_type:
            question = (request.form.get("question") or "").strip()
            mode = _read_mode(request.form)
            image_bytes, mime = _read_image_upload()
            if image_bytes and not question:
                question = asyncio.run(extract_question_from_image(_client(), image_bytes, mime))
        else:
            payload = request.get_json(silent=True) or {}
            question = (payload.get("question") or "").strip()
            mode = _read_mode(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        logger.exception("Solve failed")
        return jsonify({"error": str(exc)}), 500
    except Exception:
        logger.exception("Image parse failed")
        return jsonify({"error": "Could not read that image. Try a clearer photo."}), 500

    if not question:
        return jsonify({"error": "Paste a question or upload a photo of one."}), 400
    if len(question) > MAX_QUESTION_CHARS:
        return jsonify({"error": "Question is too long."}), 400

    try:
        result = asyncio.run(run_batch(question, mode))
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

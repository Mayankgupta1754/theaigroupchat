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
"""
| Import                | Main purpose                          |
| --------------------- | ------------------------------------- |
| `annotations`         | Better type hints                     |
| `asyncio`             | Run async operations concurrently     |
| `base64`              | Encode images/files as text           |
| `json`                | Work with JSON/API data               |
| `logging`             | Application/debug logs                |
| `os`                  | Environment variables & OS operations |
| `re`                  | Pattern matching/text extraction      |
| `time`                | Timing and timestamps                 |
| `Counter`             | Count votes/results                   |
| `Path`                | Work with files/folders               |
| `load_dotenv`         | Load `.env` variables                 |
| `Flask`               | Create web server                     |
| `jsonify`             | Return JSON from Flask                |
| `render_template`     | Serve HTML pages                      |
| `request`             | Receive user input/files              |
| `send_from_directory` | Serve files                           |
| `AsyncOpenAI`         | Make asynchronous OpenAI API calls    |
"""
# Load environment variables from .env file
load_dotenv(override=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-group-chat")
# Define the root directory of the application
ROOT = Path(__file__).resolve().parent
# Define the maximum image size in bytes
MAX_IMAGE_BYTES = 4 * 1024 * 1024
# Create the Flask application
app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)
# Configure the maximum content length for file uploads
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES + 64_000
# Define the models to use
MODELS: dict[str, str] = {
    "GPT": "openai/gpt-4o-mini",
    "Claude": "anthropic/claude-haiku-4.5",
    "Gemini": "google/gemini-2.5-flash",
    "DeepSeek": "deepseek/deepseek-chat",
}

# Define the judge name and model
JUDGE_NAME = "Judge"
JUDGE_MODEL = "openai/gpt-4o-mini"
JUDGE_SOLVE_MODEL = "openai/gpt-4o-mini"

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
    "You are a highly reliable general-purpose question-solving assistant. "
    "Solve the user's question accurately using only the information provided. "
    "The question may be a single-correct MCQ, multiple-correct MCQ, numerical, integer, "
    "true/false, reasoning, quantitative, scientific, technical, or short-answer problem. "
    "First understand exactly what is being asked, identify the relevant information, "
    "and reason through the problem carefully before selecting or calculating the answer. "
    "For multiple-choice questions, evaluate the options against the question rather than "
    "assuming that one option must be correct. For multiple-correct questions, select every "
    "option that is independently correct. For numerical and integer questions, calculate "
    "the answer directly and provide the numerical result. "
    "Do not guess, do not invent missing information, and do not answer a different question. "
    "If the question is incomplete, corrupted, unreadable, contradictory, or genuinely "
    "cannot be solved from the information provided, return SKIP. "
    "Do not return SKIP merely because the problem is difficult or requires substantial reasoning. "
    "Your response must contain exactly two lines. "
    "Line 1 must begin with 'ANSWER:' followed by the final answer. "
    "For MCQs, use the option label(s), such as A or A,C. "
    "For numerical or integer questions, provide the numerical answer. "
    "If no reliable answer can be determined, use SKIP. "
    "Line 2 must contain one concise sentence explaining the key reason for the answer, "
    "with a maximum of 18 words."
)


SOLVE_SOLVER_SYSTEM = (
    "You are an expert problem-solving tutor capable of solving challenging questions "
    "across mathematics, physics, chemistry, computer science, aptitude, reasoning, "
    "and other academic or technical subjects. "
    "Solve the given problem completely and rigorously. "
    "The problem may be a single-correct MCQ, multiple-correct MCQ, numerical, integer, "
    "short-answer, or multi-step problem. "
    "Begin by identifying the relevant information and what must be determined. "
    "Then select the appropriate concepts, principles, equations, algorithms, or formulas. "
    "Show the reasoning in a logical sequence, including the necessary setup, substitutions, "
    "calculations, simplifications, and verification of the result. "
    "For multiple-choice questions, evaluate the options carefully and explain why the "
    "selected option or options satisfy the question. "
    "For numerical or integer problems, calculate the final value explicitly and include "
    "units when they are relevant. "
    "Do not skip a problem merely because it is difficult, lengthy, unfamiliar, or not an A/B/C/D MCQ. "
    "Do not guess or invent facts, equations, values, diagrams, or assumptions that are not justified. "
    "If an assumption is genuinely necessary, state it clearly and use only reasonable assumptions "
    "supported by the problem. "
    "If the problem is genuinely impossible to solve from the information provided, return SKIP. "
    "Write mathematical expressions and formulas using LaTeX inside $...$ or $$...$$. "
    "For example, use $\\frac{a}{b}$, $v = u + at$, or $$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$. "
    "Keep the solution focused on the actual problem and avoid unnecessary exposition. "
    "End with the final answer clearly identified. "
    "Output exactly in this format:\n"
    "ANSWER: <final answer only>\n"
    "SOLUTION:\n"
    "<clear, rigorous, step-by-step solution>"
)


ANSWER_JUDGE_SYSTEM = (
    "You are the final answer judge in a multi-model question-solving system. "
    "Your task is to determine the most accurate answer to the given question. "
    "Solve the question independently before considering the candidate answers from other models. "
    "Treat the candidate answers only as supporting evidence, never as authoritative truth. "
    "Do not select an answer merely because it has majority support. "
    "If candidates disagree, identify the disagreement and determine which answer is actually "
    "supported by the question and correct reasoning. "
    "Check calculations, assumptions, option interpretation, units, and logical consistency. "
    "For multiple-correct questions, verify each selected option independently. "
    "For numerical and integer questions, verify the numerical result directly. "
    "Do not be influenced by confident wording, long explanations, or the number of models "
    "supporting an answer. A single well-reasoned answer may be correct even when the majority "
    "is wrong. "
    "Do not guess. If the question is incomplete, ambiguous, corrupted, or you cannot determine "
    "a reliable answer after careful analysis, return SKIP. "
    "Your response must contain exactly two lines. "
    "Line 1 must begin with 'ANSWER:' followed by the final answer or SKIP. "
    "Line 2 must contain one concise sentence, no more than 18 words, explaining the decisive reason."
)


SOLVE_JUDGE_SYSTEM = (
    "You are the final expert judge for a multi-model question-solving system. "
    "Independently solve and verify the given question before using any candidate solutions as evidence. "
    "The problem may involve mathematics, physics, chemistry, computer science, aptitude, reasoning, "
    "or another academic or technical domain. "
    "Do not assume that the majority answer is correct. Treat every candidate solution as potentially "
    "wrong and verify its reasoning, calculations, assumptions, and interpretation of the question. "
    "When candidate solutions disagree, determine precisely where they differ and establish which "
    "approach is mathematically, scientifically, or logically valid. "
    "Pay particular attention to hidden assumptions, arithmetic errors, incorrect formulas, "
    "units, boundary conditions, option wording, and incomplete reasoning. "
    "For multiple-correct questions, evaluate every option independently. "
    "For numerical or integer questions, independently recompute the result. "
    "Use the candidate solutions as useful evidence, but never copy an answer without verification. "
    "If the question cannot be solved reliably because required information is missing, corrupted, "
    "or genuinely ambiguous, return SKIP. "
    "Otherwise provide a rigorous, self-contained solution that another expert could verify. "
    "Write all mathematical expressions and formulas using LaTeX inside $...$ or $$...$$. "
    "Include the necessary setup, equations, reasoning, calculations, and final verification. "
    "Keep the explanation focused and avoid irrelevant commentary. "
    "Output exactly in this format:\n"
    "ANSWER: <final answer only>\n"
    "SOLUTION:\n"
    "<detailed, rigorous, step-by-step solution>"
)


EXTRACT_SYSTEM = (
    "You are a highly accurate document and image transcription assistant for question papers. "
    "Your task is to extract questions exactly as they appear in the provided image or document. "
    "Do not solve, interpret, summarize, correct, rewrite, or improve the questions. "
    "Preserve every question's wording, numbers, symbols, mathematical expressions, units, "
    "option labels, answer choices, punctuation, and relevant formatting as faithfully as possible. "
    "Pay special attention to decimal points, negative signs, exponents, fractions, superscripts, "
    "subscripts, variables, units, percentages, inequalities, and option labels because small "
    "transcription errors can change the meaning of a question. "
    "If the page contains multiple questions, separate them using a line containing exactly <<<Q>>>. "
    "If a common passage, table, diagram description, or set of instructions applies to multiple "
    "questions, include the required shared context in every corresponding question block so each "
    "question remains understandable on its own. "
    "Preserve sub-parts such as (a), (b), and (c) together when they form one question. "
    "Do not add answers, explanations, commentary, headings, or assumptions. "
    "If a portion of the text is genuinely unreadable, do not invent it; preserve what can be read "
    "and indicate the unreadable portion as [UNREADABLE]. "
    "If the provided image or document does not contain a recognizable question or question paper, "
    "output SKIP."
)


SPLIT_SYSTEM = (
    "You are a question-paper parsing assistant. "
    "Convert the provided exam or document text into a list of individual, self-contained questions. "
    "Do not solve, answer, summarize, rewrite, or correct any question. "
    "Preserve the original wording, numerical values, units, mathematical expressions, options, "
    "instructions, and relevant context as faithfully as possible. "
    "Each array element must contain exactly one complete question together with all information "
    "needed to answer it. "
    "If a common passage, paragraph, table, or set of instructions applies to multiple questions, "
    "repeat that shared context inside every relevant question so each returned question can stand alone. "
    "Keep sub-parts together when they belong to the same question. "
    "Do not incorrectly split a single multi-part problem into separate questions. "
    "Do not merge independent questions merely because they share a topic. "
    "Preserve answer choices with their corresponding question. "
    "Return ONLY valid JSON in exactly this structure:\n"
    "{\"questions\": [\"question 1\", \"question 2\"]}\n"
    "Do not wrap the JSON in Markdown code fences. "
    "Do not include any text before or after the JSON. "
    "If there is only one question, return a one-element array. "
    "If no valid question can be identified, return:\n"
    "{\"questions\": []}"
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
#11 sept 2026(2:40am)

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


_NUMBERED_Q = re.compile(
    r"(?im)^(?:(?:question|q(?:ues(?:tion)?)?|problem|prob|ex(?:ercise)?)\s*[.\-]?\s*\d{1,2}\s*[\).:]?|"
    r"\(\s*\d{1,2}\s*\)|"
    r"\[\s*\d{1,2}\s*\]|"
    r"\d{1,2}(?!\d)\s*[\).:])\s*"
)
_OPTIONS_ONLY = re.compile(
    r"(?is)^(?:[A-D][\).:\-]\s+\S.*(?:\n|$)){2,}$"
)
_QUESTION_VERB = re.compile(
    r"(?im)^\s*(?:find|calculate|solve|evaluate|determine|compute|what|which|how many|prove|show that)\b"
)


def _is_options_only(text: str) -> bool:
    return bool(_OPTIONS_ONLY.match(text.strip()))


def _looks_like_question(text: str) -> bool:
    blob = text.strip()
    if len(blob) < 12 or _is_options_only(blob):
        return False
    if "?" in blob:
        return True
    if _QUESTION_VERB.search(blob) or re.search(
        r"(?i)\b(?:find|calculate|solve|evaluate|determine|compute|what is|what are|how many)\b",
        blob,
    ):
        return True
    if re.search(r"(?im)^\s*[A-D][\).:\-]\s+\S", blob) or re.search(
        r"(?im)\n\s*[A-D][\).:\-]\s+\S", blob
    ):
        return True
    return False


def _chunks_from_matches(blob: str, matches: list[re.Match[str]]) -> list[str]:
    parts: list[str] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
        chunk = blob[match.start() : end].strip()
        if len(chunk) > 12:
            parts.append(chunk)
    return parts


def _merge_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if merged and _is_options_only(piece):
            merged[-1] = f"{merged[-1]}\n\n{piece}"
            continue
        if _looks_like_question(piece):
            if buffer:
                piece = f"{buffer}\n\n{piece}"
                buffer = ""
            merged.append(piece)
        else:
            buffer = f"{buffer}\n\n{piece}".strip() if buffer else piece
    if buffer:
        if merged:
            merged[-1] = f"{merged[-1]}\n\n{buffer}"
        else:
            merged.append(buffer)
    return merged


def split_questions_local(text: str) -> list[str]:
    blob = text.strip()
    if not blob:
        return []
    if "<<<Q>>>" in blob:
        return [part.strip() for part in blob.split("<<<Q>>>") if part.strip()][:MAX_QUESTIONS]

    numbered = list(_NUMBERED_Q.finditer(blob))
    if len(numbered) >= 2:
        parts = _chunks_from_matches(blob, numbered)
        if len(parts) >= 2:
            return parts[:MAX_QUESTIONS]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", blob) if part.strip()]
    merged = _merge_paragraphs(paragraphs)
    question_like = [part for part in merged if _looks_like_question(part)]
    if len(question_like) >= 2:
        return merged[:MAX_QUESTIONS]

    return [blob]


def looks_like_multiple(text: str) -> bool:
    blob = text.strip()
    option_blocks = len(re.findall(r"(?im)(?:^|\n)\s*A[\).\:-]\s+\S", blob))
    numbered = len(_NUMBERED_Q.findall(blob))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", blob) if part.strip()]
    question_paras = [part for part in paragraphs if _looks_like_question(part)]
    return option_blocks >= 2 or numbered >= 2 or len(question_paras) >= 2


async def split_questions(client: AsyncOpenAI, text: str) -> list[str]:
    local = split_questions_local(text)
    if len(local) >= 2:
        logger.info("Split %s questions locally", len(local))
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
        if len(items) >= 2:
            logger.info("Split %s questions via model", len(items))
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

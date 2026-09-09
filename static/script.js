const form = document.getElementById("mcq-form");
const questionEl = document.getElementById("question");
const composer = document.getElementById("composer");
const session = document.getElementById("session");
const chat = document.getElementById("chat");
const questionView = document.getElementById("question-view");
const consensusBox = document.getElementById("consensus-box");
const askBtn = document.getElementById("ask-btn");
const statusChip = document.getElementById("status-chip");
const againBtn = document.getElementById("again-btn");

const HANDLES = ["GPT", "Claude", "Gemini", "DeepSeek"];

function setStatus(text) {
  statusChip.textContent = text;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function addMessage(html, extraClass = "") {
  const node = document.createElement("article");
  node.className = `msg ${extraClass}`.trim();
  node.innerHTML = html;
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;
  return node;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function renderThinking() {
  chat.innerHTML = "";
  HANDLES.forEach((name) => {
    addMessage(
      `<div class="msg-h"><span class="handle">${name}</span></div>
       <div class="detail">…</div>`,
      "thinking"
    );
  });
}

function fillModelCard(index, model) {
  const card = chat.children[index];
  if (!card) return;
  card.classList.remove("thinking");
  const ok = model.status === "success";
  const skipped = model.status === "skipped";
  card.classList.toggle("error", !ok && !skipped);
  card.classList.toggle("skipped", skipped);
  const reason = escapeHtml(model.reason || "");
  if (ok) {
    card.innerHTML = `<div class="msg-h"><span class="handle">${model.name}</span><span>${model.time}s</span></div>
       <div class="answer">${escapeHtml(model.answer)}</div>
       <div class="why">${reason}</div>`;
  } else if (skipped) {
    card.innerHTML = `<div class="msg-h"><span class="handle">${model.name}</span></div>
       <div class="answer">SKIP</div>
       <div class="why">${reason || "Not sure enough to answer."}</div>`;
  } else {
    card.innerHTML = `<div class="msg-h"><span class="handle">${model.name}</span></div>
       <div class="answer">—</div>
       <div class="why">${reason || "Unavailable."}</div>`;
  }
}

async function playPanel(result) {
  const models = result.models || [];
  const ordered = [...models].sort((a, b) => (a.time || 0) - (b.time || 0));

  for (const model of ordered) {
    const index = HANDLES.indexOf(model.name);
    if (index >= 0) fillModelCard(index, model);
    await sleep(180);
  }

  const c = result.consensus || {};
  const agree = c.agree || 0;
  const total = c.total || 0;
  const final = result.final_answer || result.reveal || "";
  const judgeReason = (result.judge && result.judge.reason) || "";
  const label = final
    ? `<div class="agree">${escapeHtml(String(final))}</div>
       <div class="detail">${agree} / ${total}${judgeReason ? ` · ${escapeHtml(judgeReason)}` : ""}</div>`
    : `<div class="detail">No confident answer</div>`;
  consensusBox.innerHTML = label;
  againBtn.classList.remove("hidden");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionEl.value.trim();
  if (!question) return;

  askBtn.disabled = true;
  composer.classList.add("hidden");
  session.classList.remove("hidden");
  againBtn.classList.add("hidden");
  questionView.textContent = question;
  consensusBox.innerHTML = `<span class="muted">—</span>`;
  setStatus("LIVE");
  renderThinking();

  try {
    const response = await fetch("/api/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Failed.");
    await playPanel(data);
    setStatus("DONE");
  } catch (error) {
    addMessage(
      `<div class="msg-h"><span class="handle">SYS</span></div>
       <div class="answer">—</div>
       <div class="why">${escapeHtml(error.message || "Error")}</div>`,
      "error"
    );
    setStatus("ERROR");
    againBtn.classList.remove("hidden");
  } finally {
    askBtn.disabled = false;
  }
});

againBtn.addEventListener("click", () => {
  session.classList.add("hidden");
  composer.classList.remove("hidden");
  againBtn.classList.add("hidden");
  setStatus("IDLE");
  questionEl.focus();
});

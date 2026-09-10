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
const imageInput = document.getElementById("image-input");
const dropzone = document.getElementById("dropzone");
const dropzoneCopy = document.getElementById("dropzone-copy");
const imagePreview = document.getElementById("image-preview");
const previewImg = document.getElementById("preview-img");
const previewName = document.getElementById("preview-name");
const clearImageBtn = document.getElementById("clear-image");
const uploadHint = document.getElementById("upload-hint");

const HANDLES = ["GPT", "Claude", "Gemini", "DeepSeek"];
let selectedImage = null;
let previewUrl = null;

function setStatus(text) {
  statusChip.textContent = text;
}

function setHint(text, isError = false) {
  uploadHint.textContent = text;
  uploadHint.classList.toggle("error", isError);
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
       <div class="detail">Reading the question…</div>`,
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

function clearImage(resetHint = true) {
  selectedImage = null;
  imageInput.value = "";
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
  previewImg.removeAttribute("src");
  imagePreview.classList.add("hidden");
  dropzoneCopy.classList.remove("hidden");
  if (resetHint) {
    setHint("We’ll transcribe the image into the box above so you can edit it before asking.");
  }
}

async function parseImage(file) {
  const body = new FormData();
  body.append("image", file);
  const response = await fetch("/api/parse-image", { method: "POST", body });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not read that image.");
  return data.question;
}

async function handleImageFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    setHint("Please choose an image file.", true);
    return;
  }
  if (file.size > 4 * 1024 * 1024) {
    setHint("Image is too large. Keep it under 4 MB.", true);
    return;
  }

  selectedImage = file;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  previewImg.src = previewUrl;
  previewName.textContent = file.name || "Photo attached";
  dropzoneCopy.classList.add("hidden");
  imagePreview.classList.remove("hidden");
  setHint("Reading the question from your photo…");
  askBtn.disabled = true;
  setStatus("Reading");

  try {
    const transcribed = await parseImage(file);
    questionEl.value = transcribed;
    setHint("Transcribed. Edit anything that looks off, then ask the panel.");
    setStatus("Ready");
  } catch (error) {
    setHint(error.message || "Could not read that image.", true);
    setStatus("Error");
  } finally {
    askBtn.disabled = false;
  }
}

dropzone.addEventListener("click", (event) => {
  if (event.target.closest("#clear-image")) return;
  imageInput.click();
});

imageInput.addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  handleImageFile(file);
});

["dragenter", "dragover"].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag");
  });
});

["dragleave", "drop"].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  handleImageFile(file);
});

clearImageBtn.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  clearImage();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  let question = questionEl.value.trim();

  askBtn.disabled = true;

  try {
    if (!question && selectedImage) {
      setStatus("Reading");
      setHint("Reading the question from your photo…");
      question = await parseImage(selectedImage);
      questionEl.value = question;
    }
    if (!question) {
      setHint("Paste a question or upload a photo first.", true);
      return;
    }

    composer.classList.add("hidden");
    session.classList.remove("hidden");
    againBtn.classList.add("hidden");
    questionView.textContent = question;
    consensusBox.innerHTML = `<span class="muted">Waiting for the panel</span>`;
    setStatus("Live");
    renderThinking();

    const response = await fetch("/api/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Failed.");
    await playPanel(data);
    setStatus("Done");
  } catch (error) {
    if (composer.classList.contains("hidden")) {
      addMessage(
        `<div class="msg-h"><span class="handle">System</span></div>
         <div class="answer">—</div>
         <div class="why">${escapeHtml(error.message || "Error")}</div>`,
        "error"
      );
      againBtn.classList.remove("hidden");
    } else {
      setHint(error.message || "Something went wrong.", true);
    }
    setStatus("Error");
  } finally {
    askBtn.disabled = false;
  }
});

againBtn.addEventListener("click", () => {
  session.classList.add("hidden");
  composer.classList.remove("hidden");
  againBtn.classList.add("hidden");
  setStatus("Ready");
  questionEl.focus();
});

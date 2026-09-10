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
const qNav = document.getElementById("q-nav");
const cameraBtn = document.getElementById("camera-btn");
const cameraModal = document.getElementById("camera-modal");
const cameraVideo = document.getElementById("camera-video");
const cameraCancel = document.getElementById("camera-cancel");
const shutterBtn = document.getElementById("shutter-btn");
const cameraHint = document.getElementById("camera-hint");

const HANDLES = ["GPT", "Claude", "Gemini", "DeepSeek"];
let selectedImage = null;
let previewUrl = null;
let cameraStream = null;
let sessionItems = [];
let currentMode = "answer";

function selectedMode() {
  const picked = document.querySelector('input[name="mode"]:checked');
  return picked && picked.value === "solve" ? "solve" : "answer";
}

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
       <div class="detail">Working…</div>`,
      "thinking"
    );
  });
}

function fillModelCard(index, model, mode) {
  const card = chat.children[index];
  if (!card) return;
  card.classList.remove("thinking");
  const ok = model.status === "success";
  const skipped = model.status === "skipped";
  card.classList.toggle("error", !ok && !skipped);
  card.classList.toggle("skipped", skipped);
  const reason = escapeHtml(model.reason || "");
  if (ok) {
    const body = mode === "solve" && model.reason
      ? `<div class="solution">${reason}</div>`
      : `<div class="why">${reason}</div>`;
    card.innerHTML = `<div class="msg-h"><span class="handle">${model.name}</span><span>${model.time}s</span></div>
       <div class="answer">${escapeHtml(model.answer)}</div>
       ${body}`;
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

function renderNav(items, activeIndex) {
  if (items.length < 2) {
    qNav.classList.add("hidden");
    qNav.innerHTML = "";
    return;
  }
  qNav.classList.remove("hidden");
  qNav.innerHTML = items
    .map(
      (item, index) =>
        `<button type="button" class="q-tab${index === activeIndex ? " active" : ""}" data-index="${index}">Q${item.index || index + 1}</button>`
    )
    .join("");
}

async function playItem(result, mode) {
  renderThinking();
  questionView.textContent = result.question || "";
  const models = result.models || [];
  const ordered = [...models].sort((a, b) => (a.time || 0) - (b.time || 0));

  for (const model of ordered) {
    const index = HANDLES.indexOf(model.name);
    if (index >= 0) fillModelCard(index, model, mode);
    await sleep(120);
  }

  const c = result.consensus || {};
  const agree = c.agree || 0;
  const total = c.total || 0;
  const final = result.final_answer || result.reveal || "";
  const judgeReason = (result.judge && result.judge.reason) || "";
  const solution = mode === "solve" && judgeReason
    ? `<div class="host-solution">${escapeHtml(judgeReason)}</div>`
    : "";
  consensusBox.innerHTML = final
    ? `<div class="agree">${escapeHtml(String(final))}</div>
       <div class="detail">${agree} / ${total}${mode === "answer" && judgeReason ? ` · ${escapeHtml(judgeReason)}` : ""}</div>
       ${solution}`
    : `<div class="detail">No confident answer</div>${solution}`;
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

function stopCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  cameraVideo.srcObject = null;
  cameraModal.classList.add("hidden");
}

async function openCamera() {
  cameraHint.textContent = "Point the camera at the question, then capture.";
  cameraHint.classList.remove("error");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setHint("Live camera is not available in this browser. Upload a photo instead.", true);
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    cameraVideo.srcObject = cameraStream;
    cameraModal.classList.remove("hidden");
    await cameraVideo.play().catch(() => {});
  } catch (error) {
    setHint("Could not open the camera. Allow permission, or upload a photo.", true);
  }
}

function captureFrame() {
  if (!cameraVideo.videoWidth) {
    cameraHint.textContent = "Wait for the camera to start, then try again.";
    cameraHint.classList.add("error");
    return;
  }
  const canvas = document.createElement("canvas");
  const maxWidth = 1600;
  const scale = Math.min(1, maxWidth / cameraVideo.videoWidth);
  canvas.width = Math.round(cameraVideo.videoWidth * scale);
  canvas.height = Math.round(cameraVideo.videoHeight * scale);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(cameraVideo, 0, 0, canvas.width, canvas.height);
  canvas.toBlob(
    (blob) => {
      if (!blob) {
        cameraHint.textContent = "Could not capture that frame.";
        cameraHint.classList.add("error");
        return;
      }
      const file = new File([blob], "capture.jpg", { type: "image/jpeg" });
      stopCamera();
      handleImageFile(file);
    },
    "image/jpeg",
    0.85
  );
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

cameraBtn.addEventListener("click", (event) => {
  event.preventDefault();
  openCamera();
});
cameraCancel.addEventListener("click", stopCamera);
shutterBtn.addEventListener("click", captureFrame);
cameraModal.addEventListener("click", (event) => {
  if (event.target === cameraModal) stopCamera();
});

qNav.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-index]");
  if (!tab) return;
  const index = Number(tab.dataset.index);
  if (Number.isNaN(index) || !sessionItems[index]) return;
  renderNav(sessionItems, index);
  playItem(sessionItems[index], currentMode);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  let question = questionEl.value.trim();
  const mode = selectedMode();

  askBtn.disabled = true;

  try {
    if (!question && selectedImage) {
      setStatus("Reading");
      setHint("Reading the question from your photo…");
      question = await parseImage(selectedImage);
      questionEl.value = question;
    }
    if (!question) {
      setHint("Paste a question, capture a photo, or upload one first.", true);
      return;
    }

    composer.classList.add("hidden");
    session.classList.remove("hidden");
    againBtn.classList.add("hidden");
    questionView.textContent = question;
    consensusBox.innerHTML = `<span class="muted">Waiting for the panel</span>`;
    qNav.classList.add("hidden");
    setStatus("Live");
    renderThinking();

    const response = await fetch("/api/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Failed.");
    currentMode = data.mode || mode;
    sessionItems = Array.isArray(data.items) && data.items.length ? data.items : [data];
    renderNav(sessionItems, 0);
    await playItem(sessionItems[0], currentMode);
    setStatus(data.truncated ? "First 5" : "Done");
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
  sessionItems = [];
  setStatus("Ready");
  questionEl.focus();
});

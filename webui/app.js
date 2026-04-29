const videoSelect = document.getElementById("videoSelect");
const videoPlayer = document.getElementById("videoPlayer");
const summaryStatus = document.getElementById("summaryStatus");
const summarySections = document.getElementById("summarySections");
const transcriptBox = document.getElementById("transcriptBox");

let heartbeatTimer = null;
let movementOptions = [];
let currentVideoName = null;

const SECTION_EMOJI = {
  "关键问题": "❗",
  "改进建议": "💡",
  "下次训练检查点": "✅",
};

async function loadMovements() {
  try {
    const res = await fetch("/api/movements");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    movementOptions = data.movements || [];
  } catch (err) {
    movementOptions = [];
    console.error("Failed to load movements:", err);
  }
}

function setStatus(text, isError = false) {
  summaryStatus.textContent = text || "";
  summaryStatus.dataset.error = isError ? "1" : "";
}

function flashStatus(text, durationMs = 1500) {
  setStatus(text);
  setTimeout(() => {
    if (summaryStatus.textContent === text) setStatus("");
  }, durationMs);
}

function buildMovementSelect(currentValue) {
  const sel = document.createElement("select");
  sel.className = "movement-select";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "未指定";
  sel.appendChild(placeholder);

  // If the saved value isn't in the canonical list (e.g. a composite tag like
  // "抓举/高翻"), surface it as an extra option so it isn't silently dropped.
  const knownValues = new Set(movementOptions);
  const extras = currentValue && !knownValues.has(currentValue) ? [currentValue] : [];

  for (const mv of [...movementOptions, ...extras]) {
    const opt = document.createElement("option");
    opt.value = mv;
    opt.textContent = mv;
    sel.appendChild(opt);
  }

  sel.value = currentValue || "";
  return sel;
}

async function postBulletUpdate(sectionTitle, bulletId, fields) {
  if (!currentVideoName) throw new Error("No video selected");
  const body = {
    video: currentVideoName,
    section: sectionTitle,
    bullet_id: bulletId,
    ...fields,
  };
  const res = await fetch("/api/summary/bullet", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const errBody = await res.json();
      if (errBody.error) errMsg = errBody.error;
    } catch (_) {
      /* ignore */
    }
    throw new Error(errMsg);
  }
  return res.json();
}

async function postBulletDelete(sectionTitle, bulletId) {
  if (!currentVideoName) throw new Error("No video selected");
  const res = await fetch("/api/summary/bullet/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video: currentVideoName,
      section: sectionTitle,
      bullet_id: bulletId,
    }),
  });
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const errBody = await res.json();
      if (errBody.error) errMsg = errBody.error;
    } catch (_) {
      /* ignore */
    }
    throw new Error(errMsg);
  }
  return res.json();
}

function autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = textarea.scrollHeight + "px";
}

/**
 * Build a single bullet row. Each row owns its own "saved baseline" so we can
 * (a) tell whether the textarea is dirty (Update button enabled), and (b) revert
 * the dropdown if a save fails. We never blow away the row from outside, so the
 * user's in-progress edits are preserved across other bullets' saves.
 */
function renderBulletItem(sectionTitle, bullet) {
  const li = document.createElement("li");
  li.className = "bullet-item";

  let savedMovement = bullet.movement || "";
  let savedText = bullet.text;

  const sel = buildMovementSelect(bullet.movement);

  const textarea = document.createElement("textarea");
  textarea.className = "bullet-text-input";
  textarea.value = bullet.text;
  textarea.rows = 1;
  textarea.spellcheck = false;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "bullet-update-btn";
  btn.textContent = "Update";

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "bullet-delete-btn";
  deleteBtn.textContent = "Delete";

  const refreshButton = () => {
    const trimmed = textarea.value.trim();
    btn.disabled = !trimmed || trimmed === savedText.trim();
    if (trimmed !== savedText.trim()) {
      li.classList.add("dirty");
    } else {
      li.classList.remove("dirty");
    }
  };

  textarea.addEventListener("input", () => {
    autoResize(textarea);
    refreshButton();
  });
  // After the row is in the DOM (next frame), size the textarea to its content.
  requestAnimationFrame(() => autoResize(textarea));
  refreshButton();

  sel.addEventListener("change", async () => {
    const desired = sel.value;
    sel.disabled = true;
    setStatus("Saving...");
    try {
      await postBulletUpdate(sectionTitle, bullet.id, { movement: desired });
      savedMovement = desired;
      flashStatus(`已保存: [${sectionTitle}] 动作 → ${desired || "未指定"}`);
    } catch (err) {
      sel.value = savedMovement;
      setStatus(`保存失败: ${err.message}`, true);
    } finally {
      sel.disabled = false;
    }
  });

  btn.addEventListener("click", async () => {
    const newText = textarea.value.trim();
    if (!newText) return;
    btn.disabled = true;
    textarea.disabled = true;
    setStatus("Saving...");
    try {
      await postBulletUpdate(sectionTitle, bullet.id, { text: newText });
      savedText = newText;
      // Reflect canonical (trimmed/whitespace-collapsed) form in the textarea.
      textarea.value = newText;
      autoResize(textarea);
      refreshButton();
      flashStatus(`已保存: [${sectionTitle}] 文本已更新`);
    } catch (err) {
      setStatus(`保存失败: ${err.message}`, true);
      refreshButton();
    } finally {
      textarea.disabled = false;
    }
  });

  deleteBtn.addEventListener("click", async () => {
    if (!window.confirm("Delete this bullet point?")) return;
    sel.disabled = true;
    textarea.disabled = true;
    btn.disabled = true;
    deleteBtn.disabled = true;
    setStatus("Deleting...");
    try {
      await postBulletDelete(sectionTitle, bullet.id);
      flashStatus(`已删除: [${sectionTitle}] 第 ${bullet.id + 1} 条`);
      await loadSummary(currentVideoName);
    } catch (err) {
      setStatus(`删除失败: ${err.message}`, true);
      sel.disabled = false;
      textarea.disabled = false;
      refreshButton();
      deleteBtn.disabled = false;
    }
  });

  li.appendChild(sel);
  li.appendChild(textarea);
  li.appendChild(btn);
  li.appendChild(deleteBtn);
  return li;
}

function renderSummary(data) {
  summarySections.innerHTML = "";
  const sections = (data && data.sections) || [];
  if (!sections.length) {
    summarySections.innerHTML =
      `<p class="hint">No summary yet. Re-run extract_training_log.py for this video.</p>`;
    return;
  }

  for (const section of sections) {
    const subpanel = document.createElement("div");
    subpanel.className = "subpanel summary-section";

    const header = document.createElement("h2");
    const emoji = SECTION_EMOJI[section.title] || "";
    header.textContent = emoji ? `${emoji} ${section.title}` : section.title;
    subpanel.appendChild(header);

    if (!section.bullets || !section.bullets.length) {
      const empty = document.createElement("p");
      empty.className = "hint section-empty";
      empty.textContent = "（无）";
      subpanel.appendChild(empty);
      summarySections.appendChild(subpanel);
      continue;
    }

    const list = document.createElement("ul");
    list.className = "bullet-list";
    for (const bullet of section.bullets) {
      list.appendChild(renderBulletItem(section.title, bullet));
    }
    subpanel.appendChild(list);
    summarySections.appendChild(subpanel);
  }
}

async function loadSummary(videoName) {
  setStatus("Loading summary...");
  try {
    const res = await fetch(`/api/summary?video=${encodeURIComponent(videoName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.summary || !data.summary.trim()) {
      summarySections.innerHTML =
        `<p class="hint">No summary found yet. Re-run extract_training_log.py for this video.</p>`;
      setStatus("");
      return;
    }
    renderSummary(data);
    setStatus("");
  } catch (err) {
    summarySections.innerHTML = "";
    setStatus(`Failed to load summary: ${err.message}`, true);
  }
}

async function loadTranscript(videoName) {
  transcriptBox.textContent = "Loading transcript...";
  try {
    const res = await fetch(`/api/transcript?video=${encodeURIComponent(videoName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    transcriptBox.textContent =
      data.transcript?.trim() ||
      "No transcript found yet. Re-run extract_training_log.py for this video.";
  } catch (err) {
    transcriptBox.textContent = `Failed to load transcript: ${err.message}`;
  }
}

async function loadVideos() {
  try {
    const res = await fetch("/api/videos");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const videos = (data.videos || []).slice().sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" })
    );

    videoSelect.innerHTML = "";
    if (!videos.length) {
      summarySections.innerHTML = `<p class="hint">No MP4 videos found.</p>`;
      transcriptBox.textContent = "";
      return;
    }

    for (const item of videos) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent =
        item.comment_count > 0
          ? `${item.name} (${item.comment_count})`
          : item.name;
      videoSelect.appendChild(option);
    }

    const preferred =
      videos.find((item) => item.comment_count > 0)?.name ?? videos[0].name;
    videoSelect.value = preferred;
    currentVideoName = preferred;
    videoPlayer.src = `/${encodeURIComponent(preferred)}`;
    await Promise.all([
      loadSummary(preferred),
      loadTranscript(preferred),
    ]);
  } catch (err) {
    summarySections.innerHTML = "";
    setStatus(`Failed to load videos: ${err.message}`, true);
    transcriptBox.textContent = "";
  }
}

videoSelect.addEventListener("change", async () => {
  const videoName = videoSelect.value;
  currentVideoName = videoName;
  videoPlayer.src = `/${encodeURIComponent(videoName)}`;
  await Promise.all([
    loadSummary(videoName),
    loadTranscript(videoName),
  ]);
});

function startHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    fetch("/api/heartbeat").catch(() => {});
  }, 5000);
}

function stopHeartbeat() {
  if (!heartbeatTimer) return;
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

window.addEventListener("beforeunload", () => {
  stopHeartbeat();
  navigator.sendBeacon("/api/heartbeat");
});

(async () => {
  startHeartbeat();
  await loadMovements();
  await loadVideos();
})();

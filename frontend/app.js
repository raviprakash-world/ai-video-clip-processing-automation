const state = {
  uploadId: null,
  videoMeta: null,
  parsedJson: null,
  clips: [],
  selected: new Set(),
  overlayAssetId: null,
  jobId: null,
  pollHandle: null,
  publishJobId: null,
  publishPollHandle: null,
};

const SAMPLE_JSON = {
  schema_version: "1.0",
  analysis: { source_type: "video", language: "en", total_clips_found: 2, analysis_status: "complete" },
  clips: [
    {
      clip_id: "clip_001", rank: 1, start_time: "00:05:20", end_time: "00:06:15", duration_seconds: 55,
      viral_score: 92, category: "prediction", speaker: "Guest",
      hook: "AI isn't going to replace your job, but this specific person will.",
      title_options: { curiosity: "Who Is Actually Taking Your Job?", direct: "Why AI Won't Replace You (Yet)" },
      caption: "Everyone is panicking about AI taking over, but the real threat is much closer to home.",
      hashtags: ["#ai", "#artificialintelligence", "#futureofwork", "#careeradvice", "#techtrends", "#innovation"],
      reason: "High curiosity hook addressing a universal fear of job loss.",
      payoff: "The viewer learns that adapting to AI tools is important.",
      context_warning: null, copyright_warning: null,
    },
    {
      clip_id: "clip_002", rank: 2, start_time: "00:18:10", end_time: "00:18:55", duration_seconds: 45,
      viral_score: 88, category: "insight", speaker: "Guest",
      hook: "We are completely misunderstanding what AGI actually means.",
      title_options: { curiosity: "The Truth About AGI Nobody Is Telling You", direct: "Defining Artificial General Intelligence" },
      caption: "Stop worrying about Terminator-style AI. The reality of AGI is much more subtle.",
      hashtags: ["#agi", "#tech", "#machinelearning", "#futuretech"],
      reason: "Challenges a common misconception immediately.",
      payoff: "Delivers a clearer understanding of AGI.",
      context_warning: null, copyright_warning: null,
    },
  ],
};

function $(id) { return document.getElementById(id); }

function setStatus(el, message, kind) {
  el.textContent = message;
  el.className = "status" + (kind ? " " + kind : "");
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || `Request failed (${res.status})`);
    err.payload = data;
    throw err;
  }
  return data;
}

// --- Step 1: upload -----------------------------------------------------
function showVideoProgress(label) {
  $("video-progress").hidden = false;
  $("video-progress-label").textContent = label;
  setVideoProgressPct(0);
}

function setVideoProgressPct(pct) {
  const fill = $("video-progress-fill");
  if (pct === null || pct === undefined) {
    fill.classList.add("indeterminate");
    fill.style.width = "";
    $("video-progress-pct").textContent = "";
  } else {
    fill.classList.remove("indeterminate");
    fill.style.width = `${pct}%`;
    $("video-progress-pct").textContent = `${pct.toFixed(0)}%`;
  }
}

function hideVideoProgress() {
  $("video-progress").hidden = true;
}

function onVideoReady(data, verb) {
  state.uploadId = data.upload_id;
  state.videoMeta = data.video;
  const v = data.video;
  setStatus(
    $("video-status"),
    `${verb}. Duration ${v.duration_seconds.toFixed(1)}s, ${v.width}x${v.height}, ` +
      `${v.video_codec}/${v.audio_codec || "no audio"}.`,
    "ok"
  );
  $("step-json").hidden = false;
}

$("upload-btn").addEventListener("click", () => {
  const fileInput = $("video-file");
  if (!fileInput.files.length) {
    setStatus($("video-status"), "Choose a video file first.", "err");
    return;
  }
  const file = fileInput.files[0];
  const form = new FormData();
  form.append("file", file);

  setStatus($("video-status"), "", "");
  showVideoProgress("Uploading...");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/video/upload");

  // Real byte-level upload progress from the browser -- not simulated.
  xhr.upload.addEventListener("progress", (ev) => {
    if (ev.lengthComputable) {
      setVideoProgressPct((ev.loaded / ev.total) * 100);
    } else {
      setVideoProgressPct(null);
    }
  });

  xhr.addEventListener("load", () => {
    hideVideoProgress();
    let data = {};
    try {
      data = JSON.parse(xhr.responseText);
    } catch (e) {
      /* fall through to status check below */
    }
    if (xhr.status >= 200 && xhr.status < 300) {
      onVideoReady(data, "Uploaded");
    } else {
      setStatus($("video-status"), `Upload failed: ${data.message || xhr.statusText}`, "err");
    }
  });

  xhr.addEventListener("error", () => {
    hideVideoProgress();
    setStatus($("video-status"), "Upload failed: network error.", "err");
  });

  xhr.send(form);
});

$("fetch-url-btn").addEventListener("click", async () => {
  const url = $("video-url").value.trim();
  if (!url) {
    setStatus($("video-status"), "Paste a video URL first.", "err");
    return;
  }

  setStatus($("video-status"), "", "");
  showVideoProgress("Starting download...");

  try {
    const started = await api("/api/video/ingest-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    pollIngestProgress(started.ingest_id);
  } catch (e) {
    hideVideoProgress();
    setStatus($("video-status"), `URL ingestion failed: ${e.message}`, "err");
  }
});

function pollIngestProgress(ingestId) {
  const tick = async () => {
    let state_;
    try {
      state_ = await api(`/api/video/ingest-url/${ingestId}`);
    } catch (e) {
      hideVideoProgress();
      setStatus($("video-status"), `URL ingestion failed: ${e.message}`, "err");
      return;
    }

    if (state_.status === "downloading") {
      $("video-progress-label").textContent = "Downloading...";
      if (state_.progress_pct !== null && state_.progress_pct !== undefined) {
        setVideoProgressPct(state_.progress_pct);
      } else {
        const mb = (state_.downloaded_bytes / (1024 * 1024)).toFixed(1);
        setVideoProgressPct(null);
        $("video-progress-pct").textContent = `${mb} MB`;
      }
      setTimeout(tick, 700);
      return;
    }

    hideVideoProgress();
    if (state_.status === "completed") {
      onVideoReady(state_, `Fetched (${state_.source_type})`);
    } else {
      setStatus($("video-status"), `URL ingestion failed: ${state_.error?.message || "unknown error"}`, "err");
    }
  };
  tick();
}

// --- Step 2: JSON validation ---------------------------------------------
$("load-sample-btn").addEventListener("click", () => {
  $("json-input").value = JSON.stringify(SAMPLE_JSON, null, 2);
});

$("validate-btn").addEventListener("click", async () => {
  const text = $("json-input").value.trim();
  if (!text) {
    setStatus($("json-status"), "Paste the AI analysis JSON first.", "err");
    return;
  }
  setStatus($("json-status"), "Validating...", "");
  try {
    const data = await api("/api/analysis/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ json_text: text, upload_id: state.uploadId }),
    });
    state.parsedJson = JSON.parse(text);
    state.clips = data.clips;
    state.selected = new Set(data.clips.filter((c) => c.valid).map((c) => c.clip_id));
    const invalidCount = data.clips.filter((c) => !c.valid).length;
    setStatus(
      $("json-status"),
      `Valid JSON. ${data.clips.length} clip(s) found` + (invalidCount ? `, ${invalidCount} out of range.` : "."),
      "ok"
    );
    renderClips();
    $("step-clips").hidden = false;
    $("step-config").hidden = false;
  } catch (e) {
    setStatus($("json-status"), `Validation failed [${e.payload?.error || "ERROR"}]: ${e.message}`, "err");
    $("step-clips").hidden = true;
  }
});

function renderClips() {
  const container = $("clips-list");
  container.innerHTML = "";
  for (const clip of state.clips.sort((a, b) => a.rank - b.rank)) {
    const row = document.createElement("div");
    row.className = "clip-row" + (clip.valid ? "" : " invalid");
    const checked = state.selected.has(clip.clip_id) ? "checked" : "";
    const disabled = clip.valid ? "" : "disabled";
    row.innerHTML = `
      <label style="flex-direction:row;align-items:center;gap:8px;">
        <input type="checkbox" data-clip-id="${clip.clip_id}" ${checked} ${disabled} />
        <div>
          <div class="clip-title">#${clip.rank} ${clip.start_time} &rarr; ${clip.end_time} &middot; ${clip.duration_seconds}s
            <span class="badge score">Score ${clip.viral_score}</span>
          </div>
          <div class="clip-meta">${clip.clip_id} &middot; ${clip.category} &middot; ${clip.speaker}</div>
          ${clip.error ? `<div class="clip-error">${clip.error.message}</div>` : ""}
        </div>
      </label>
    `;
    container.appendChild(row);
  }
  container.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", (ev) => {
      const id = ev.target.getAttribute("data-clip-id");
      if (ev.target.checked) state.selected.add(id);
      else state.selected.delete(id);
    });
  });
}

// --- Step 4: config + overlay ---------------------------------------------
document.querySelectorAll('input[name="watermark"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    $("overlay-controls").hidden = document.querySelector('input[name="watermark"]:checked').value !== "authorized_overlay";
  });
});

async function maybeUploadOverlay() {
  const mode = document.querySelector('input[name="watermark"]:checked').value;
  if (mode !== "authorized_overlay") return null;
  const authorized = $("overlay-authorized").checked;
  const fileInput = $("overlay-file");
  if (!authorized) throw new Error("Please confirm you are authorized to add this overlay.");
  if (!fileInput.files.length) throw new Error("Choose a PNG overlay image.");
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  const data = await api("/api/watermark/upload", { method: "POST", body: form });
  return data.overlay_asset_id;
}

// --- Step 4b: generate -----------------------------------------------------
async function buildProcessingConfig() {
  const watermarkMode = document.querySelector('input[name="watermark"]:checked').value;
  const overlayAssetId = await maybeUploadOverlay();
  return {
    output_width: parseInt($("cfg-width").value, 10),
    output_height: parseInt($("cfg-height").value, 10),
    crop_strategy: document.querySelector('input[name="crop"]:checked').value,
    watermark: {
      mode: watermarkMode,
      authorized: watermarkMode !== "none" ? true : false,
      overlay_asset_id: overlayAssetId,
    },
  };
}

$("generate-btn").addEventListener("click", async () => {
  if (!state.selected.size) {
    setStatus($("config-status"), "Select at least one clip.", "err");
    return;
  }
  setStatus($("config-status"), "Preparing job...", "");
  try {
    const config = await buildProcessingConfig();

    const job = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_id: state.uploadId,
        json_data: state.parsedJson,
        selected_clip_ids: Array.from(state.selected),
        config,
      }),
    });
    state.jobId = job.job_id;
    setStatus($("config-status"), `Job ${job.job_id} started.`, "ok");
    $("step-results").hidden = false;
    startPolling();
  } catch (e) {
    setStatus($("config-status"), `Could not start job: ${e.message}`, "err");
  }
});

// --- Auto-Publish: Generate + Queue (section 24) ----------------------------
$("generate-queue-btn").addEventListener("click", async () => {
  const platforms = Array.from(document.querySelectorAll('input[name="publish-platform"]:checked')).map((el) => el.value);
  if (!platforms.length) {
    setStatus($("queue-generate-status"), "Select at least one platform to auto-publish to.", "err");
    return;
  }
  if (!state.parsedJson) {
    setStatus($("queue-generate-status"), "Validate the AI JSON in Step 2 first.", "err");
    return;
  }
  setStatus($("queue-generate-status"), "Generating clips and building the publish queue...", "");
  try {
    const config = await buildProcessingConfig();
    const interval_minutes = parseInt($("publish-interval").value, 10) || 60;

    const result = await api("/api/publishing/queue/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_id: state.uploadId, json_data: state.parsedJson, config, platforms, interval_minutes }),
    });

    state.publishJobId = result.job_id;
    let msg = `Job ${result.job_id} started -- clips will be queued for ${platforms.join(", ")} once generated.`;
    if (result.skipped_clips && result.skipped_clips.length) {
      msg += ` (${result.skipped_clips.length} clip(s) skipped: out of range.)`;
    }
    setStatus($("queue-generate-status"), msg, "ok");
    $("step-dashboard").hidden = false;
    startDashboardPolling();
  } catch (e) {
    setStatus($("queue-generate-status"), `Could not start: ${e.message}`, "err");
  }
});

function startDashboardPolling() {
  if (state.publishPollHandle) clearInterval(state.publishPollHandle);
  state.publishPollHandle = setInterval(pollDashboard, 4000);
  pollDashboard();
}

async function pollDashboard() {
  if (!state.publishJobId) return;
  try {
    const items = await api(`/api/publishing/queue?job_id=${state.publishJobId}`);
    renderDashboard(items);
  } catch (e) {
    // transient poll failure -- try again next tick
  }
}

function formatCountdown(scheduledAtIso) {
  const diffMs = new Date(scheduledAtIso).getTime() - Date.now();
  if (diffMs <= 0) return "due now";
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `in ${hours}h ${minutes % 60}m`;
}

function renderDashboard(items) {
  const upcoming = items
    .filter((i) => ["WAITING", "RETRYING", "QUOTA_WAIT"].includes(i.status))
    .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));

  const nextEl = $("dashboard-next");
  if (upcoming.length) {
    const next = upcoming[0];
    nextEl.innerHTML = `<strong>Next:</strong> ${next.clip_id} &rarr; ${next.platform} ${formatCountdown(next.scheduled_at)} (${new Date(next.scheduled_at).toLocaleString()})`;
  } else {
    nextEl.textContent = items.length ? "No more clips waiting to publish." : "No queued items yet.";
  }

  const tbody = $("queue-table-body");
  tbody.innerHTML = "";
  const sorted = [...items].sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));
  for (const item of sorted) {
    const tr = document.createElement("tr");
    const canRetry = ["FAILED", "AUTH_REQUIRED", "NOT_SUPPORTED", "QUOTA_WAIT", "CANCELLED"].includes(item.status);
    const canSkipOrCancel = ["WAITING", "RETRYING", "PAUSED", "QUOTA_WAIT"].includes(item.status);
    const canPublishNow = ["WAITING", "PAUSED"].includes(item.status);

    tr.innerHTML = `
      <td>${item.clip_id}${item.title ? `<div class="clip-meta">${item.title}</div>` : ""}</td>
      <td>${item.platform}</td>
      <td><span class="status-pill ${item.status}">${item.status}</span></td>
      <td>${new Date(item.scheduled_at).toLocaleString()}</td>
      <td>${item.attempt_count}</td>
      <td class="error-cell">${item.last_error || (item.external_post_id ? `id: ${item.external_post_id}` : "")}</td>
      <td class="row-actions">
        ${canRetry ? `<button class="secondary" data-action="retry" data-id="${item.id}">Retry</button>` : ""}
        ${canSkipOrCancel ? `<button class="secondary" data-action="skip" data-id="${item.id}">Skip</button>` : ""}
        ${canPublishNow ? `<button class="secondary" data-action="publish-now" data-id="${item.id}">Publish Now</button>` : ""}
      </td>
    `;
    tbody.appendChild(tr);
  }

  tbody.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.getAttribute("data-action");
      const id = btn.getAttribute("data-id");
      try {
        await api(`/api/publishing/queue/${id}/${action}`, { method: "POST" });
        pollDashboard();
      } catch (e) {
        alert(`Action failed: ${e.message}`);
      }
    });
  });
}

$("pause-queue-btn").addEventListener("click", async () => {
  if (!state.publishJobId) return;
  await api(`/api/publishing/queue/job/${state.publishJobId}/pause`, { method: "POST" });
  pollDashboard();
});

$("resume-queue-btn").addEventListener("click", async () => {
  if (!state.publishJobId) return;
  await api(`/api/publishing/queue/job/${state.publishJobId}/resume`, { method: "POST" });
  pollDashboard();
});

// --- Connected Accounts ------------------------------------------------------
async function loadAccountsAndCapabilities() {
  try {
    const [capabilities, accounts] = await Promise.all([
      api("/api/publishing/capabilities"),
      api("/api/publishing/accounts"),
    ]);
    const container = $("accounts-list");
    container.innerHTML = "";
    for (const cap of capabilities) {
      const account = accounts.find((a) => a.platform === cap.platform);
      const row = document.createElement("div");
      row.className = "account-row";
      row.innerHTML = `
        <span class="platform-name">${cap.platform}</span>
        <span>${account ? account.account_name || account.account_id : "not connected"}</span>
        <span class="status-pill ${cap.status}">${cap.status.replace("_", " ")}</span>
      `;
      row.title = cap.notes.join(" ");
      container.appendChild(row);
    }
  } catch (e) {
    $("accounts-list").textContent = "Could not load account status.";
  }
}

function showOAuthRedirectStatus() {
  const params = new URLSearchParams(window.location.search);
  const oauth = params.get("oauth");
  const message = params.get("message");
  if (oauth) {
    setStatus($("oauth-status"), message || "", oauth === "success" ? "ok" : "err");
    window.history.replaceState({}, "", window.location.pathname);
  }
}

loadAccountsAndCapabilities();
showOAuthRedirectStatus();

// --- Step 5: progress + results ---------------------------------------------
function startPolling() {
  if (state.pollHandle) clearInterval(state.pollHandle);
  state.pollHandle = setInterval(pollJob, 1200);
  pollJob();
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    renderProgress(job);
    if (["COMPLETED", "FAILED", "CANCELLED"].includes(job.status)) {
      clearInterval(state.pollHandle);
      renderResults(job);
    }
  } catch (e) {
    clearInterval(state.pollHandle);
  }
}

function renderProgress(job) {
  const container = $("progress-list");
  container.innerHTML = `<div class="clip-meta">Job status: <strong>${job.status}</strong></div>`;
  for (const clip of job.clips) {
    const div = document.createElement("div");
    div.className = "progress-row";
    div.innerHTML = `
      <div class="progress-label"><span>${clip.clip_id} (${clip.status})</span><span>${clip.progress_pct}%</span></div>
      <div class="progress-bar-bg"><div class="progress-bar-fill" style="width:${clip.progress_pct}%"></div></div>
      ${clip.error ? `<div class="clip-error">${clip.error.message}</div>` : ""}
    `;
    container.appendChild(div);
  }
}

function renderResults(job) {
  const container = $("results-list");
  container.innerHTML = "";
  const completed = job.clips.filter((c) => c.status === "COMPLETED");
  for (const clip of completed) {
    const url = `/api/jobs/${job.job_id}/clips/${clip.clip_id}/file`;
    const row = document.createElement("div");
    row.className = "result-row";
    row.innerHTML = `
      <div>
        <video src="${url}" controls muted></video>
        <div class="clip-meta">${clip.output_file}</div>
      </div>
      <div class="result-actions">
        <a class="button secondary" href="${url}" download>Download</a>
      </div>
    `;
    container.appendChild(row);
  }
  if (completed.length) {
    const link = $("download-all");
    link.href = `/api/jobs/${job.job_id}/download-all`;
    link.hidden = false;
  }
}

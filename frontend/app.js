const state = {
  uploadId: null,
  videoMeta: null,
  parsedJson: null,
  clips: [],
  selected: new Set(),
  overlayAssetId: null,
  jobId: null,
  pollHandle: null,
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

$("upload-btn").addEventListener("click", async () => {
  const fileInput = $("video-file");
  if (!fileInput.files.length) {
    setStatus($("video-status"), "Choose a video file first.", "err");
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  setStatus($("video-status"), "Uploading and validating video...", "");
  try {
    const data = await api("/api/video/upload", { method: "POST", body: form });
    onVideoReady(data, "Uploaded");
  } catch (e) {
    setStatus($("video-status"), `Upload failed: ${e.message}`, "err");
  }
});

$("fetch-url-btn").addEventListener("click", async () => {
  const url = $("video-url").value.trim();
  if (!url) {
    setStatus($("video-status"), "Paste a video URL first.", "err");
    return;
  }
  setStatus($("video-status"), "Downloading and validating video from URL...", "");
  try {
    const data = await api("/api/video/ingest-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    onVideoReady(data, `Fetched (${data.source_type})`);
  } catch (e) {
    setStatus($("video-status"), `URL ingestion failed: ${e.message}`, "err");
  }
});

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
$("generate-btn").addEventListener("click", async () => {
  if (!state.selected.size) {
    setStatus($("config-status"), "Select at least one clip.", "err");
    return;
  }
  setStatus($("config-status"), "Preparing job...", "");
  try {
    const watermarkMode = document.querySelector('input[name="watermark"]:checked').value;
    const overlayAssetId = await maybeUploadOverlay();

    const config = {
      output_width: parseInt($("cfg-width").value, 10),
      output_height: parseInt($("cfg-height").value, 10),
      crop_strategy: document.querySelector('input[name="crop"]:checked').value,
      watermark: {
        mode: watermarkMode,
        authorized: watermarkMode !== "none" ? true : false,
        overlay_asset_id: overlayAssetId,
      },
    };

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

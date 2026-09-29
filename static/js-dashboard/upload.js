/* ---------- Upload page ---------- */
function formatBytes(bytes) {
  if (!bytes) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? (mb / 1024).toFixed(2) + " GB" : mb.toFixed(1) + " MB";
}
function formatDuration(seconds) {
  if (!seconds || !isFinite(seconds)) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function processingStageLabel(stage, status) {
  const labels = {
    ingestion: "Uploading source",
    input_validation: "Checking video",
    media_analysis: "Analyzing media",
    audio_extraction: "Extracting audio",
    deepgram_transcription: "Transcribing speech",
    transcript_validation: "Checking transcript",
    groq_selection: "Finding the best moments",
    candidate_scoring: "Scoring clip candidates",
    clip_extraction: "Cutting selected clips",
    reframing: "Creating vertical framing",
    captions: "Preparing captions",
    audio_processing: "Processing audio",
    ffmpeg_rendering: "Rendering clips",
    output_validation: "Checking final output",
    thumbnail_generation: "Creating thumbnails",
    export: "Saving your clips",
    cleanup: "Finishing up",
    completed: "Processing complete",
    failed: "Processing failed",
  };
  return labels[stage] || labels[status] || "Processing video";
}

function uploadPageHTML() {
  return `
  <div class="nc-page">
    <h1 class="nc-display nc-page-title" style="margin-bottom:4px">Upload a video</h1>
    <p class="nc-page-sub" style="margin-bottom:28px">NaijaClip will find the best moments and turn them into clips.</p>
    <div class="nc-upload-wrap">
      ${state.uploadFile ? uploadFileCardHTML() : uploadDropzoneHTML()}
    </div>
  </div>`;
}

function uploadDropzoneHTML() {
  return `
    <div class="nc-dropzone" id="dropzone">
      <div class="nc-dropzone-icon">${ICONS.upload}</div>
      <div class="nc-dropzone-title">Drag and drop your video here</div>
      <div class="nc-dropzone-sub">MP4, MOV, or WebM — up to 5 GB</div>
      <button class="nc-btn nc-btn-outline nc-focus" id="browse-btn">Browse files</button>
      <input type="file" accept="video/*" id="file-input" style="display:none" />
    </div>`;
}

function uploadFileCardHTML() {
  const f = state.uploadFile;
  if (state.uploadJob) {
    const job = state.uploadJob;
    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const terminal = ["completed", "failed", "cancelled", "canceled"].includes(job.status);
    const failed = job.status === "failed";
    return `
    <div class="nc-file-card nc-processing-card ${failed ? "is-failed" : ""}">
      <div class="nc-file-row">
        <div class="nc-file-icon">${ICONS.fileVideo}</div>
        <div style="min-width:0;flex:1">
          <div class="nc-file-name">${f.name}</div>
          <div class="nc-file-meta">${processingStageLabel(job.current_stage, job.status)}</div>
        </div>
        <div class="nc-progress-value">${progress}%</div>
      </div>
      <div class="nc-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" aria-label="Video processing progress" style="margin-top:20px"><div class="nc-progress-bar" style="width:${progress}%"></div></div>
      <div class="nc-processing-status">
        ${terminal ? "" : '<span class="nc-spinner" aria-hidden="true"></span>'}
        <span>${failed ? "We could not finish this video." : terminal ? "Your clips are ready." : "You can leave this page open while we work."}</span>
      </div>
      ${!terminal && job.video_id ? `<button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-video-cancel="${job.video_id}" style="margin-top:14px">Cancel processing</button>` : ""}
      ${job.error_message ? `<p class="nc-page-sub">We couldn't finish this video. Please try again.</p>` : ""}
    </div>`;
  }
  return `
    <div class="nc-file-card">
      <div class="nc-file-row">
        <div class="nc-file-icon">${ICONS.fileVideo}</div>
        <div style="min-width:0;flex:1">
          <div class="nc-file-name">${f.name}</div>
          <div class="nc-file-meta">${formatDuration(f.duration)} · ${formatBytes(f.size)}</div>
        </div>
        <button class="nc-file-remove nc-focus" id="remove-file" aria-label="Remove file">${ICONS.x}</button>
      </div>
      <div style="margin-top:20px">
        <button class="nc-btn nc-btn-primary nc-btn-full nc-focus" id="start-processing">Start processing</button>
      </div>
    </div>`;
}

function handleFiles(fileList) {
  const f = fileList && fileList[0];
  if (!f) return;
  const meta = { file: f, name: f.name, size: f.size, duration: null };
  state.uploadFile = meta;
  renderDashboard();
  if (f.type && f.type.startsWith("video")) {
    try {
      const url = URL.createObjectURL(f);
      const v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = () => {
        if (state.uploadFile && state.uploadFile.name === meta.name) {
          state.uploadFile.duration = v.duration;
          if (state.page === "dashboard-upload") renderDashboard();
        }
        URL.revokeObjectURL(url);
      };
      v.src = url;
    } catch (e) {
      /* duration stays unknown */
    }
  }
}

function attachUploadHandlers() {
  const dropzone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const browseBtn = document.getElementById("browse-btn");
  if (dropzone) {
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragging");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragging");
      handleFiles(e.dataTransfer.files);
    });
  }
  if (browseBtn && input) {
    browseBtn.addEventListener("click", () => input.click());
    input.addEventListener("change", (e) => handleFiles(e.target.files));
  }
  const removeBtn = document.getElementById("remove-file");
  if (removeBtn) removeBtn.addEventListener("click", () => { state.uploadFile = null; state.uploadJob = null; renderDashboard(); });
  const startBtn = document.getElementById("start-processing");
  if (startBtn)
    startBtn.addEventListener("click", async () => {
      const file = state.uploadFile && state.uploadFile.file;
      if (!file) return;
      startBtn.disabled = true;
      startBtn.textContent = "Uploading...";
      state.uploadJob = { status: "uploading", progress: 0 };
      renderDashboard();
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("title", file.name.replace(/\.[^.]+$/, ""));
      try {
        const response = await fetch("/api/videos", {
          method: "POST",
          headers: { Authorization: `Bearer ${localStorage.getItem("naijaclip_access_token") || ""}` },
          body: form,
        });
        let data = {};
        try {
          data = await response.json();
        } catch (_ignored) {
          data = {};
        }
        if (!response.ok) {
          throw new Error(data.detail || "Upload failed. Please try again.");
        }
        state.uploadJob = { job_id: data.job_id, status: data.status, progress: 0 };
        await loadDashboardData();
        toast(`Processing started for "${file.name}"`);
        renderDashboard();
        pollJob(data.job_id);
      } catch (error) {
        startBtn.disabled = false;
        startBtn.textContent = "Start processing";
        toast("We couldn't start this upload. Please try again.", "error");
      }
    });
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    let job = {};
    try {
      job = await response.json();
    } catch (_ignored) {
      job = {};
    }
    if (!response.ok) throw new Error("Unable to read job status");
    state.uploadJob = job;
    if (state.page === "dashboard-upload") renderDashboard();
    if (!["completed", "failed", "cancelled", "canceled"].includes(job.status)) {
      window.setTimeout(() => pollJob(jobId), 2000);
    } else if (job.status === "failed") {
      toast("We couldn't finish this video. Please try again.", "error");
    } else if (job.status === "completed") {
      toast("Your clips are ready");
      await loadDashboardData();
    }
  } catch (error) {
    toast("We couldn't read the processing status. Please try again.", "error");
  }
}

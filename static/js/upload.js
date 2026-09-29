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
  const meta = { name: f.name, size: f.size, duration: null };
  state.uploadFile = meta;
  render();
  if (f.type && f.type.startsWith("video")) {
    try {
      const url = URL.createObjectURL(f);
      const v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = () => {
        if (state.uploadFile && state.uploadFile.name === meta.name) {
          state.uploadFile.duration = v.duration;
          if (state.page === "dashboard-upload") render();
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
  if (removeBtn) removeBtn.addEventListener("click", () => { state.uploadFile = null; render(); });
  const startBtn = document.getElementById("start-processing");
  if (startBtn)
    startBtn.addEventListener("click", () => {
      toast(`Processing started for "${state.uploadFile.name}"`);
      state.uploadFile = null;
      go("dashboard-videos");
    });
}

function editorEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function editorTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function editorPercent(value) {
  const duration = state.clipEditor?.clip?.duration_seconds || 1;
  return Math.max(0, Math.min(100, Number(value) / duration * 100));
}

function editorSubtitleWidthEm(text) {
  const narrow = "ilI.,'`:;!|()[]{}";
  const wide = "MW@%&";
  return [...String(text)].reduce((width, character) => {
    if (/\s/.test(character)) return width + 0.34;
    if (narrow.includes(character)) return width + 0.32;
    if (wide.includes(character)) return width + 0.9;
    if (/[A-Z0-9]/.test(character)) return width + 0.65;
    return width + 0.55;
  }, 0);
}

function clipEditorHTML() {
  const editor = state.clipEditor;
  if (!editor || editor.loading) {
    return `<div class="nc-page"><div class="nc-placeholder-box">Loading clip editor...</div></div>`;
  }
  if (editor.error) {
    return `<div class="nc-page"><div class="nc-page-head"><h1 class="nc-display nc-page-title">Clip Editor</h1><button class="nc-btn nc-btn-outline nc-btn-sm" data-nav="dashboard-clips">Back to clips</button></div><div class="nc-placeholder-box">${editorEscape(editor.error)}</div></div>`;
  }
  const clip = editor.clip;
  return `
    <div class="nc-page nc-editor-page">
      <div class="nc-editor-head">
        <div>
          <button class="nc-link nc-editor-back" data-nav="dashboard-clips">${ICONS.chevronRight} Clips</button>
          <h1 class="nc-display nc-page-title">${editorEscape(clip.title)}</h1>
        </div>
        <div class="nc-editor-head-actions">
          <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="undo" title="Undo" aria-label="Undo" ${editor.history.length ? "" : "disabled"}>Undo</button>
          <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="redo" title="Redo" aria-label="Redo" ${editor.future.length ? "" : "disabled"}>Redo</button>
          <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="save">Save edit</button>
          <button class="nc-btn nc-btn-primary nc-btn-sm" data-editor-action="export">Export</button>
        </div>
      </div>
      ${clip.has_clean_editor_source ? "" : `<div class="nc-editor-source-warning" role="status">This older clip has captions baked into its video. New subtitle edits can appear over the original captions.</div>`}

      <div class="nc-editor-workspace">
        <section class="nc-editor-monitor" aria-label="Clip preview">
          <div class="nc-editor-stage">
            <video id="nc-editor-video" src="${editorEscape(clip.source_url || clip.output_url)}" poster="${editorEscape(clip.thumbnail_url || "")}" playsinline preload="metadata"></video>
            <div id="nc-editor-subtitle-overlay" class="nc-editor-subtitle" aria-live="polite"></div>
          </div>
          <div class="nc-editor-transport">
            <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="previous" aria-label="Previous segment">Previous</button>
            <button class="nc-btn nc-btn-primary nc-btn-sm" data-editor-action="play">Play</button>
            <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="next" aria-label="Next segment">Next</button>
            <span class="nc-editor-time"><span id="nc-editor-playhead">00:00</span> / ${editorTime(clip.duration_seconds)}</span>
          </div>
        </section>

        <section class="nc-editor-timeline-panel" aria-label="Clip timeline">
          <div class="nc-editor-section-heading"><h2>Timeline</h2><span>Source ${editorTime(clip.duration_seconds)}</span></div>
          <div class="nc-editor-ruler"><span>00:00</span><span>${editorTime(clip.duration_seconds / 3)}</span><span>${editorTime(clip.duration_seconds * 2 / 3)}</span><span>${editorTime(clip.duration_seconds)}</span></div>
          <div class="nc-editor-timeline" data-editor-timeline role="group" aria-label="Clip timeline">
            <div class="nc-editor-segments" data-editor-segments></div>
            <div class="nc-editor-playhead" data-editor-playhead></div>
          </div>
          <div class="nc-editor-subtitle-track" data-editor-subtitles role="group" aria-label="Subtitle cues"></div>

          <div class="nc-editor-tools">
            <div class="nc-editor-range-fields">
              <label>Segment in <input type="number" data-editor-field="segment-start" min="0" step="0.05" /></label>
              <label>Segment out <input type="number" data-editor-field="segment-end" min="0" step="0.05" /></label>
              <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="trim-start">Set in to playhead</button>
              <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="trim-end">Set out to playhead</button>
            </div>
            <div class="nc-editor-range-fields nc-editor-remove-fields">
              <label>Remove from <input type="number" data-editor-field="remove-start" min="0" step="0.05" value="0" /></label>
              <label>Remove to <input type="number" data-editor-field="remove-end" min="0" step="0.05" value="0" /></label>
              <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="split">Split at playhead</button>
              <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="remove-section">Remove section</button>
              <button class="nc-btn nc-btn-ghost nc-btn-sm nc-editor-danger" data-editor-action="remove-segment">Remove segment</button>
            </div>
          </div>
        </section>
      </div>

      <section class="nc-editor-subtitles-panel">
        <div class="nc-editor-section-heading">
          <div><h2>Subtitles</h2><p>Edit text and timing. Cues remain aligned through cuts.</p></div>
          <button class="nc-btn nc-btn-outline nc-btn-sm" data-editor-action="add-subtitle">Add subtitle</button>
        </div>
        <div class="nc-editor-subtitle-list" id="nc-editor-subtitle-list"></div>
      </section>

      <section class="nc-editor-export-panel" aria-live="polite">
        <div class="nc-editor-section-heading"><h2>Exports</h2><span>Original clip stays unchanged</span></div>
        <div id="nc-editor-exports"></div>
      </section>
    </div>`;
}

async function editorAuthenticatedFetch(url, options = {}) {
  const send = (token) => fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${token || ""}`,
    },
  });
  let accessToken = localStorage.getItem("naijaclip_access_token");
  let response = await send(accessToken);

  if (response.status === 401) {
    const refreshToken = localStorage.getItem("naijaclip_refresh_token");
    if (refreshToken) {
      try {
        const refreshResponse = await fetch("/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        const tokens = await refreshResponse.json().catch(() => ({}));
        if (refreshResponse.ok && tokens.access_token) {
          accessToken = tokens.access_token;
          localStorage.setItem("naijaclip_access_token", accessToken);
          if (tokens.refresh_token) localStorage.setItem("naijaclip_refresh_token", tokens.refresh_token);
          response = await send(accessToken);
        }
      } catch (_error) {}
    }
  }

  if (response.status === 401) {
    localStorage.removeItem("naijaclip_access_token");
    localStorage.removeItem("naijaclip_refresh_token");
    window.location.href = "/login";
  }
  return response;
}

async function loadClipEditor(clipId) {
  if (!clipId) return;
  state.clipEditor = { requestedId: clipId, loading: true, history: [], future: [], exports: [] };
  renderDashboard();
  try {
    const response = await editorAuthenticatedFetch(`/api/clips/${encodeURIComponent(clipId)}/edit`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Unable to open this clip (server returned ${response.status}).`);
    state.clipEditor = {
      requestedId: clipId,
      clip: data.clip,
      configuration: data.configuration,
      exports: data.exports || [],
      playhead: 0,
      selectedSegment: 0,
      selectedCue: null,
      history: [],
      future: [],
      loading: false,
      saving: false,
      exporting: false,
    };
    renderDashboard();
    attachClipEditorPlayer();
    refreshClipEditor();
    const latest = state.clipEditor.exports[0];
    if (latest && ["queued", "processing"].includes(latest.status)) pollClipExport(latest.id);
  } catch (error) {
    const message = error instanceof TypeError
      ? "Could not reach the clip service. Check your connection and try again."
      : error.message || "Unable to open this clip.";
    state.clipEditor = { requestedId: clipId, error: message, loading: false, history: [], future: [] };
    renderDashboard();
  }
}

function attachClipEditorPlayer() {
  const player = document.getElementById("nc-editor-video");
  if (!player) return;
  player.onloadedmetadata = () => {
    const playhead = state.clipEditor?.playhead || 0;
    if (Math.abs(player.currentTime - playhead) > 0.1) player.currentTime = playhead;
    refreshClipEditor();
  };
  player.ontimeupdate = () => {
    const editor = state.clipEditor;
    if (!editor || editor.loading) return;
    const time = player.currentTime;
    const segments = editor.configuration.segments;
    const currentIndex = segments.findIndex((segment) => time >= segment.start - 0.02 && time < segment.end - 0.03);
    if (currentIndex < 0) {
      const next = segments.find((segment) => segment.start > time + 0.02);
      if (next) player.currentTime = next.start;
      else if (time >= segments[segments.length - 1].end - 0.04) player.pause();
      return;
    }
    if (time >= segments[currentIndex].end - 0.03) {
      const next = segments[currentIndex + 1];
      if (next) player.currentTime = next.start;
      else player.pause();
      if (!next) editor.playhead = segments[currentIndex].end;
      return;
    }
    editor.playhead = time;
    refreshEditorPlayhead();
  };
}

function refreshClipEditor() {
  const editor = state.clipEditor;
  if (!editor || editor.loading || editor.error || !editor.configuration) return;
  const segment = editor.configuration.segments[editor.selectedSegment] || editor.configuration.segments[0];
  const start = document.querySelector('[data-editor-field="segment-start"]');
  const end = document.querySelector('[data-editor-field="segment-end"]');
  if (start && segment) start.value = segment.start.toFixed(2);
  if (end && segment) end.value = segment.end.toFixed(2);
  renderEditorTimeline();
  const cueList = document.getElementById("nc-editor-subtitle-list");
  if (cueList) cueList.innerHTML = editor.configuration.subtitles.map((cue) => `
    <div class="nc-editor-cue ${editor.selectedCue === cue.id ? "active" : ""}" data-cue-id="${editorEscape(cue.id)}">
      <button class="nc-editor-cue-time" type="button" data-editor-seek="${cue.start}" aria-label="Seek to subtitle">${editorTime(cue.start)}–${editorTime(cue.end)}</button>
      <label><span>Text</span><textarea data-subtitle-text="${editorEscape(cue.id)}" maxlength="4000" rows="2">${editorEscape(cue.text)}</textarea></label>
      ${editorSubtitleWidthEm(cue.text) > 18 ? `<small class="nc-editor-warning" role="status">Too wide for one line. Shorten before saving; your text is kept.</small>` : ""}
      <label><span>Start</span><input data-subtitle-time="start" data-cue-id="${editorEscape(cue.id)}" type="number" min="0" max="${editor.clip.duration_seconds}" step="0.05" value="${Number(cue.start).toFixed(2)}" /></label>
      <label><span>End</span><input data-subtitle-time="end" data-cue-id="${editorEscape(cue.id)}" type="number" min="0" max="${editor.clip.duration_seconds}" step="0.05" value="${Number(cue.end).toFixed(2)}" /></label>
      <button class="nc-btn nc-btn-ghost nc-btn-sm nc-editor-danger" data-delete-subtitle="${editorEscape(cue.id)}" aria-label="Delete subtitle">Delete</button>
    </div>`).join("") || `<div class="nc-editor-empty">No subtitles. Add a cue at the playhead.</div>`;
  refreshEditorPlayhead();
  refreshEditorExports();
  const undo = document.querySelector('[data-editor-action="undo"]');
  const redo = document.querySelector('[data-editor-action="redo"]');
  if (undo) undo.disabled = editor.history.length === 0;
  if (redo) redo.disabled = editor.future.length === 0;
  const save = document.querySelector('[data-editor-action="save"]');
  const exportButton = document.querySelector('[data-editor-action="export"]');
  if (save) save.disabled = editor.saving || editor.exporting;
  if (exportButton) exportButton.disabled = editor.exporting || editor.saving;
}

function renderEditorTimeline() {
  const editor = state.clipEditor;
  const segmentLayer = document.querySelector("[data-editor-segments]");
  if (segmentLayer) {
    segmentLayer.innerHTML = editor.configuration.segments.map((item, index) => `
      <div class="nc-editor-segment ${index === editor.selectedSegment ? "active" : ""}" style="left:${editorPercent(item.start)}%;width:${editorPercent(item.end - item.start)}%" data-segment-index="${index}">
        <button type="button" class="nc-editor-handle start" data-editor-handle="start" data-segment-index="${index}" aria-label="Adjust segment ${index + 1} in point"></button>
        <button type="button" class="nc-editor-handle end" data-editor-handle="end" data-segment-index="${index}" aria-label="Adjust segment ${index + 1} out point"></button>
      </div>`).join("");
  }
  const subtitleLayer = document.querySelector("[data-editor-subtitles]");
  if (subtitleLayer) {
    subtitleLayer.innerHTML = editor.configuration.subtitles.map((cue) => `
      <button type="button" class="nc-editor-timeline-cue ${editor.selectedCue === cue.id ? "active" : ""}"
        style="left:${editorPercent(cue.start)}%;width:${Math.max(0.8, editorPercent(cue.end - cue.start))}%"
        data-editor-seek="${cue.start}" data-editor-cue-id="${editorEscape(cue.id)}"
        title="${editorEscape(cue.text)}: ${editorTime(cue.start)}–${editorTime(cue.end)}"
        aria-label="${editorEscape(cue.text)}, ${editorTime(cue.start)} to ${editorTime(cue.end)}"></button>`).join("");
  }
}

function refreshEditorPlayhead() {
  const editor = state.clipEditor;
  if (!editor || !editor.configuration) return;
  const line = document.querySelector("[data-editor-playhead]");
  if (line) line.style.left = `${editorPercent(editor.playhead)}%`;
  const label = document.getElementById("nc-editor-playhead");
  if (label) label.textContent = editorTime(editor.playhead);
  const playButton = document.querySelector('[data-editor-action="play"]');
  const player = document.getElementById("nc-editor-video");
  if (playButton) playButton.textContent = player && !player.paused ? "Pause" : "Play";
  const overlay = document.getElementById("nc-editor-subtitle-overlay");
  if (overlay) {
    const cue = editor.configuration.subtitles.find((item) => editor.playhead >= item.start && editor.playhead < item.end);
    overlay.textContent = cue ? cue.text : "";
    if (cue) {
      const lineWidth = Math.max(editorSubtitleWidthEm(cue.text), 1);
      overlay.style.fontSize = `${Math.min(18, overlay.clientWidth / lineWidth)}px`;
    }
  }
}

function refreshEditorExports() {
  const target = document.getElementById("nc-editor-exports");
  const editor = state.clipEditor;
  if (!target || !editor) return;
  target.innerHTML = editor.exports.length ? editor.exports.map((item) => `
    <div class="nc-editor-export-row">
      <div><strong>${editorEscape(editorExportLabel(item))}</strong><span>${editorEscape(item.error_message || "")}</span></div>
      ${item.status === "completed" && item.output_url ? `<a class="nc-btn nc-btn-outline nc-btn-sm" href="${editorEscape(item.output_url)}" target="_blank" rel="noreferrer">Play / download</a><video class="nc-editor-export-video" controls playsinline preload="metadata" src="${editorEscape(item.output_url)}"></video>` : `<span class="nc-editor-export-progress">${editorEscape(editorExportLabel(item))}</span>`}
    </div>`).join("") : `<div class="nc-editor-empty">No exports yet.</div>`;
}

function editorExportLabel(item) {
  if (item.status === "completed") return "Complete";
  if (item.status === "failed") return item.error_message || "Export failed";
  const stage = item.job?.current_stage;
  if (stage === "editor_source") return "Processing source...";
  if (stage === "editor_rendering") return "Rendering...";
  if (stage === "editor_upload") return "Uploading...";
  return item.status === "processing" ? "Processing..." : "Preparing export...";
}

function cloneEditorConfiguration(configuration) {
  return JSON.parse(JSON.stringify(configuration));
}

function editorValidateConfiguration(configuration) {
  const editor = state.clipEditor;
  const duration = Number(editor.clip.duration_seconds);
  if (!configuration.segments.length) throw new Error("Keep at least one video segment.");
  configuration.segments.sort((a, b) => a.start - b.start);
  for (let index = 0; index < configuration.segments.length; index += 1) {
    const segment = configuration.segments[index];
    if (!Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.start < 0 || segment.end > duration || segment.end - segment.start < 0.08) throw new Error("Each segment must be at least 0.08 seconds and stay within the clip.");
    if (index && configuration.segments[index - 1].end > segment.start) throw new Error("Video segments cannot overlap.");
  }
  configuration.subtitles.sort((a, b) => a.start - b.start);
  for (let index = 0; index < configuration.subtitles.length; index += 1) {
    const cue = configuration.subtitles[index];
    cue.text = String(cue.text ?? "").replace(/\s+/g, " ").trim();
    if (!cue.text || cue.text.length > 4000 || cue.start < 0 || cue.end > duration || cue.end - cue.start < 0.04) throw new Error("Subtitle text and timing must be valid.");
    if (index && configuration.subtitles[index - 1].end > cue.start) throw new Error("Subtitle cues cannot overlap.");
  }
  return configuration;
}

function editorChange(change) {
  const editor = state.clipEditor;
  const before = cloneEditorConfiguration(editor.configuration);
  try {
    editorValidateConfiguration(change(editor.configuration));
    if (JSON.stringify(before) !== JSON.stringify(editor.configuration)) {
      editor.history.push(before);
      editor.history = editor.history.slice(-40);
      editor.future = [];
    }
  } catch (error) {
    editor.configuration = before;
    toast(error.message, "error");
  }
  refreshClipEditor();
}

function editorCommitSubtitleText(field) {
  const editor = state.clipEditor;
  const before = field.dataset.beforeEdit;
  delete field.dataset.beforeEdit;
  const snapshot = before ? JSON.parse(before) : null;
  if (snapshot && JSON.stringify(snapshot) !== JSON.stringify(editor.configuration)) {
    editor.history.push(snapshot);
    editor.history = editor.history.slice(-40);
    editor.future = [];
  }
  refreshClipEditor();
}

function editorSplitAtPlayhead() {
  editorChange((configuration) => {
    const index = configuration.segments.findIndex((segment) => state.clipEditor.playhead > segment.start + 0.08 && state.clipEditor.playhead < segment.end - 0.08);
    if (index < 0) throw new Error("Place the playhead inside a segment to split it.");
    const segment = configuration.segments[index];
    configuration.segments.splice(index, 1, { start: segment.start, end: state.clipEditor.playhead }, { start: state.clipEditor.playhead, end: segment.end });
    state.clipEditor.selectedSegment = index + 1;
    return configuration;
  });
}

function editorRemoveSection(start, end) {
  editorChange((configuration) => {
    if (end - start < 0.08) throw new Error("Set a valid section to remove.");
    const remaining = [];
    for (const segment of configuration.segments) {
      if (end <= segment.start || start >= segment.end) remaining.push(segment);
      else {
        if (start - segment.start >= 0.08) remaining.push({ start: segment.start, end: Math.min(start, segment.end) });
        if (segment.end - end >= 0.08) remaining.push({ start: Math.max(end, segment.start), end: segment.end });
      }
    }
    if (!remaining.length) throw new Error("Keep at least one video segment.");
    configuration.segments = remaining;
    state.clipEditor.selectedSegment = Math.min(state.clipEditor.selectedSegment, remaining.length - 1);
    return configuration;
  });
}

function editorTrimToPlayhead(edge) {
  const editor = state.clipEditor;
  const segment = editor.configuration.segments[editor.selectedSegment];
  if (!segment) return;
  editorChange((configuration) => {
    const selected = configuration.segments[editor.selectedSegment];
    selected[edge] = editor.playhead;
    return configuration;
  });
}

function editorUndoRedo(direction) {
  const editor = state.clipEditor;
  const source = direction === "undo" ? editor.history : editor.future;
  const destination = direction === "undo" ? editor.future : editor.history;
  if (!source.length) return;
  destination.push(cloneEditorConfiguration(editor.configuration));
  editor.configuration = source.pop();
  editor.selectedSegment = Math.min(editor.selectedSegment, editor.configuration.segments.length - 1);
  refreshClipEditor();
}

function editorSeek(time) {
  const editor = state.clipEditor;
  const player = document.getElementById("nc-editor-video");
  if (!editor || !player) return;
  const nextTime = Math.max(0, Math.min(editor.clip.duration_seconds, Number(time) || 0));
  editor.playhead = nextTime;
  player.currentTime = nextTime;
  refreshEditorPlayhead();
}

function editorMoveSegment(direction) {
  const editor = state.clipEditor;
  const nextIndex = Math.max(0, Math.min(editor.configuration.segments.length - 1, editor.selectedSegment + direction));
  editor.selectedSegment = nextIndex;
  const segment = editor.configuration.segments[nextIndex];
  editorSeek(segment.start);
  refreshClipEditor();
}

function editorTogglePlayback() {
  const player = document.getElementById("nc-editor-video");
  if (!player) return;
  if (!player.paused) {
    player.pause();
    return;
  }
  const editor = state.clipEditor;
  if (!editor.configuration.segments.some((segment) => editor.playhead >= segment.start && editor.playhead < segment.end)) {
    const next = editor.configuration.segments.find((segment) => segment.start >= editor.playhead) || editor.configuration.segments[0];
    editorSeek(next.start);
  }
  player.play().catch(() => toast("Unable to play this clip.", "error"));
}

async function saveClipEditor() {
  const editor = state.clipEditor;
  editor.saving = true;
  refreshClipEditor();
  try {
    const response = await editorAuthenticatedFetch(`/api/clips/${encodeURIComponent(editor.clip.id)}/edit`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ configuration: editor.configuration }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Unable to save this edit.");
    toast("Edit saved");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    editor.saving = false;
    refreshClipEditor();
  }
}

async function exportClipEditor() {
  const editor = state.clipEditor;
  editor.exporting = true;
  refreshClipEditor();
  try {
    const response = await editorAuthenticatedFetch(`/api/clips/${encodeURIComponent(editor.clip.id)}/exports`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ configuration: editor.configuration }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Unable to start export.");
    const item = { id: data.export_id, status: data.status, job: { id: data.job_id, current_stage: "queued", progress: 0 } };
    editor.exports.unshift(item);
    refreshEditorExports();
    pollClipExport(item.id);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    editor.exporting = false;
    refreshClipEditor();
  }
}

async function pollClipExport(exportId, attempts = 0) {
  const editor = state.clipEditor;
  if (!editor || !editor.clip || attempts >= 240) return;
  try {
    const response = await editorAuthenticatedFetch(`/api/clip-exports/${encodeURIComponent(exportId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Unable to check export status.");
    const index = editor.exports.findIndex((item) => item.id === exportId);
    if (index >= 0) editor.exports[index] = data;
    refreshEditorExports();
    if (data.status === "completed") toast("Edited clip is ready");
    else if (data.status === "failed") toast(data.error_message || "Export failed", "error");
    else window.setTimeout(() => pollClipExport(exportId, attempts + 1), 1500);
  } catch (error) {
    toast(error.message, "error");
  }
}
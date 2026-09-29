async function updateFeaturedClip(button) {
  const clipId = button.dataset.featureClip;
  const action = button.dataset.featureAction;
  const featureUrl = `/api/clips/${encodeURIComponent(clipId)}/feature`;
  setButtonLoading(button, true, action === "add" ? "Featuring..." : action === "remove" ? "Removing..." : "Saving order...");

  try {
    let response;
    if (action === "add" || action === "remove") {
      response = await fetch(featureUrl, {
        method: action === "add" ? "POST" : "DELETE",
        credentials: "include",
      });
    } else {
      const featured = state.dashboardData.clips
        .filter((clip) => clip.is_featured)
        .sort((left, right) => left.featured_order - right.featured_order);
      const from = featured.findIndex((clip) => clip.id === clipId);
      const to = from + (action === "up" ? -1 : 1);
      if (from < 0 || to < 0 || to >= featured.length) return;
      [featured[from], featured[to]] = [featured[to], featured[from]];
      response = await fetch("/api/homepage/featured-clips/order", {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clip_ids: featured.map((clip) => clip.id) }),
      });
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Unable to update homepage clips.");
    toast(action === "add" ? "Clip featured on homepage" : action === "remove" ? "Clip removed from homepage" : "Homepage clip order updated");
  } catch (error) {
    toast(error.message || "Unable to update homepage clips.", "error");
  } finally {
    await loadDashboardData();
  }
}

document.addEventListener("click", (e) => {
  const featureButton = e.target.closest("[data-feature-clip]");
  if (featureButton) {
    e.preventDefault();
    updateFeaturedClip(featureButton);
    return;
  }

  const editClipButton = e.target.closest("[data-edit-clip]");
  if (editClipButton) {
    e.preventDefault();
    const clipId = editClipButton.getAttribute("data-edit-clip");
    window.location.href = `/dashboard?page=dashboard-editor&clip=${encodeURIComponent(clipId)}`;
    return;
  }

  const editorAction = e.target.closest("[data-editor-action]");
  if (editorAction) {
    e.preventDefault();
    const action = editorAction.getAttribute("data-editor-action");
    const editor = state.clipEditor;
    if (!editor || !editor.configuration) return;
    if (action === "play") editorTogglePlayback();
    else if (action === "previous") editorMoveSegment(-1);
    else if (action === "next") editorMoveSegment(1);
    else if (action === "split") editorSplitAtPlayhead();
    else if (action === "trim-start") editorTrimToPlayhead("start");
    else if (action === "trim-end") editorTrimToPlayhead("end");
    else if (action === "remove-section") {
      const start = Number(document.querySelector('[data-editor-field="remove-start"]').value);
      const end = Number(document.querySelector('[data-editor-field="remove-end"]').value);
      editorRemoveSection(start, end);
    } else if (action === "remove-segment") {
      editorChange((configuration) => {
        if (configuration.segments.length === 1) throw new Error("Keep at least one video segment.");
        configuration.segments.splice(editor.selectedSegment, 1);
        editor.selectedSegment = Math.min(editor.selectedSegment, configuration.segments.length - 1);
        return configuration;
      });
    } else if (action === "add-subtitle") {
      const duration = Number(editor.clip.duration_seconds);
      let start = Math.min(editor.playhead, duration - 0.5);
      start = Math.max(0, start);
      const end = Math.min(duration, start + 2);
      editorChange((configuration) => {
        const cue = { id: `cue-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`, start, end, text: "New subtitle" };
        configuration.subtitles.push(cue);
        editor.selectedCue = cue.id;
        return configuration;
      });
    } else if (action === "undo" || action === "redo") editorUndoRedo(action);
    else if (action === "save") saveClipEditor();
    else if (action === "export") exportClipEditor();
    return;
  }

  const seekButton = e.target.closest("[data-editor-seek]");
  if (seekButton) {
    if (seekButton.dataset.editorCueId) state.clipEditor.selectedCue = seekButton.dataset.editorCueId;
    editorSeek(Number(seekButton.getAttribute("data-editor-seek")));
    if (seekButton.dataset.editorCueId) refreshClipEditor();
    return;
  }

  const deleteCue = e.target.closest("[data-delete-subtitle]");
  if (deleteCue) {
    const cueId = deleteCue.getAttribute("data-delete-subtitle");
    editorChange((configuration) => {
      configuration.subtitles = configuration.subtitles.filter((cue) => cue.id !== cueId);
      return configuration;
    });
    return;
  }

  const timeline = e.target.closest("[data-editor-timeline]");
  if (timeline) {
    if (e.target.closest("[data-editor-handle]")) return;
    const segmentEl = e.target.closest("[data-segment-index]");
    if (segmentEl) state.clipEditor.selectedSegment = Number(segmentEl.dataset.segmentIndex);
    const rect = timeline.getBoundingClientRect();
    editorSeek((e.clientX - rect.left) / rect.width * state.clipEditor.clip.duration_seconds);
    refreshClipEditor();
    return;
  }

  const cancelButton = e.target.closest("[data-video-cancel]");
  if (cancelButton) {
    e.preventDefault();
    const videoId = cancelButton.getAttribute("data-video-cancel");
    if (!window.confirm("Cancel processing for this video?")) return;
    setButtonLoading(cancelButton, true, "Cancelling...");
    fetch(`/api/videos/${videoId}/cancel`, {
      method: "POST",
      headers: { Authorization: `Bearer ${localStorage.getItem("naijaclip_access_token") || ""}` },
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to cancel this video.");
      toast("Video processing cancelled");
      await loadDashboardData();
    }).catch((error) => {
      setButtonLoading(cancelButton, false);
      toast(error.message, "error");
    });
    return;
  }

  const deleteButton = e.target.closest("[data-video-delete]");
  if (deleteButton) {
    e.preventDefault();
    const videoId = deleteButton.getAttribute("data-video-delete");
    if (!window.confirm("Delete this video and its generated clips?")) return;
    setButtonLoading(deleteButton, true, "Deleting...");
    fetch(`/api/videos/${videoId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${localStorage.getItem("naijaclip_access_token") || ""}` },
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to delete this video.");
      toast("Video deleted");
      await loadDashboardData();
    }).catch((error) => {
      setButtonLoading(deleteButton, false);
      toast(error.message, "error");
    });
    return;
  }

  const navEl = e.target.closest("[data-nav]");
  if (navEl) {
    e.preventDefault();
    const page = navEl.getAttribute("data-nav");
    if (page === "landing") {
      window.location.href = "/";
      return;
    }
    window.location.href = `/dashboard?page=${encodeURIComponent(page)}`;
    return;
  }

  const logoutEl = e.target.closest("[data-logout]");
  if (logoutEl) {
    e.preventDefault();
    endAuthenticationSession().finally(() => { window.location.replace("/login"); });
    return;
  }

  const modalOpen = e.target.closest("[data-clip-modal-open]");
  if (modalOpen) {
    e.preventDefault();
    openClipModal({
      output_url: modalOpen.getAttribute("data-clip-url"),
      thumbnail_url: modalOpen.getAttribute("data-clip-poster"),
      title: modalOpen.getAttribute("data-clip-title") || "Clip preview",
    });
    return;
  }

  const modalClose = e.target.closest("[data-clip-modal-close]");
  if (modalClose) {
    closeClipModal();
    return;
  }

  const toastEl = e.target.closest("[data-toast]");
  if (toastEl) { toast(toastEl.getAttribute("data-toast")); return; }
  if (e.target.closest("[data-open-mobile]")) { state.mobileOpen = true; renderDashboard(); return; }
  if (e.target.closest("[data-close-mobile]")) { state.mobileOpen = false; renderDashboard(); return; }

  const billingButton = e.target.closest('#init-pro-payment');
  if (billingButton) {
    setButtonLoading(billingButton, true, 'Opening checkout...');
    const statusEl = document.getElementById('billing-status');
    if (statusEl) statusEl.textContent = 'Creating a test-mode paystack checkout...';
    (async () => {
      try {
        const response = await fetch('/payments/paystack/initialize', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('naijaclip_access_token') || ''}`
          },
          body: JSON.stringify({ amount: '2500', currency: 'NGN', plan: 'pro' })
        });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Unable to start checkout.');
      if (statusEl) {
        statusEl.textContent = 'Checkout started in test mode. The subscription will stay pending until approval; no automatic Pro upgrade happens.';
      }
      if (data.authorization_url) {
        toast('Checkout is ready. Redirecting to Paystack...');
        window.location.href = data.authorization_url;
      }
      } catch (error) {
        const message = error.message || 'Unable to start payment.';
        if (statusEl) statusEl.textContent = message;
        toast(message, 'error');
      } finally {
        setButtonLoading(billingButton, false);
      }
    })();
    return;
  }
});

document.addEventListener("keydown", (e) => {
  if (state.page === "dashboard-editor" && !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
    if (e.code === "Space") {
      e.preventDefault();
      editorTogglePlayback();
      return;
    }
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      editorSeek(state.clipEditor.playhead + (e.key === "ArrowLeft" ? -1 : 1));
      return;
    }
  }
  if (e.key === "Escape") {
    const modal = document.getElementById("nc-clip-modal");
    if (modal && modal.classList.contains("visible")) {
      closeClipModal();
    }
  }
});

document.addEventListener("change", (e) => {
  const field = e.target.closest("[data-editor-field]");
  if (field && field.dataset.editorField.startsWith("segment-")) {
    const editor = state.clipEditor;
    editorChange((configuration) => {
      const segment = configuration.segments[editor.selectedSegment];
      segment[field.dataset.editorField === "segment-start" ? "start" : "end"] = Number(field.value);
      return configuration;
    });
    return;
  }

  const subtitleTime = e.target.closest("[data-subtitle-time]");
  if (subtitleTime) {
    const cueId = subtitleTime.dataset.cueId;
    editorChange((configuration) => {
      const cue = configuration.subtitles.find((item) => item.id === cueId);
      cue[subtitleTime.dataset.subtitleTime] = Number(subtitleTime.value);
      return configuration;
    });
  }
});

document.addEventListener("focusin", (e) => {
  const field = e.target.closest("[data-subtitle-text]");
  if (field && state.clipEditor?.configuration) {
    field.dataset.beforeEdit = JSON.stringify(cloneEditorConfiguration(state.clipEditor.configuration));
  }
});

document.addEventListener("input", (e) => {
  const field = e.target.closest("[data-subtitle-text]");
  if (!field || !state.clipEditor?.configuration) return;
  const cue = state.clipEditor.configuration.subtitles.find((item) => item.id === field.dataset.subtitleText);
  if (!cue) return;
  cue.text = field.value;
  refreshEditorPlayhead();
});

document.addEventListener("change", (e) => {
  const field = e.target.closest("[data-subtitle-text]");
  if (field) editorCommitSubtitleText(field);
});

document.addEventListener("pointerdown", (e) => {
  const handle = e.target.closest("[data-editor-handle]");
  if (handle && state.clipEditor?.configuration) {
    e.preventDefault();
    const index = Number(handle.dataset.segmentIndex);
    state.clipEditor.selectedSegment = index;
    state.clipEditor.drag = {
      index,
      edge: handle.dataset.editorHandle,
      before: cloneEditorConfiguration(state.clipEditor.configuration),
    };
    handle.setPointerCapture?.(e.pointerId);
    document.querySelectorAll(".nc-editor-segment").forEach((element, segmentIndex) => {
      element.classList.toggle("active", segmentIndex === index);
    });
    return;
  }
});

document.addEventListener("pointermove", (e) => {
  const editor = state.clipEditor;
  if (!editor?.drag) return;
  const timeline = document.querySelector("[data-editor-timeline]");
  if (!timeline) return;
  const rect = timeline.getBoundingClientRect();
  const duration = Number(editor.clip.duration_seconds);
  let time = Math.max(0, Math.min(duration, (e.clientX - rect.left) / rect.width * duration));
  const { index, edge } = editor.drag;
  const segment = editor.configuration.segments[index];
  if (edge === "start") {
    time = Math.max(index ? editor.configuration.segments[index - 1].end : 0, Math.min(segment.end - 0.08, time));
    segment.start = time;
  } else {
    time = Math.min(index + 1 < editor.configuration.segments.length ? editor.configuration.segments[index + 1].start : duration, Math.max(segment.start + 0.08, time));
    segment.end = time;
  }
  editor.playhead = time;
  document.querySelectorAll(".nc-editor-segment").forEach((element, segmentIndex) => {
    const item = editor.configuration.segments[segmentIndex];
    element.style.left = `${editorPercent(item.start)}%`;
    element.style.width = `${editorPercent(item.end - item.start)}%`;
    element.classList.toggle("active", segmentIndex === index);
  });
  const playhead = document.querySelector("[data-editor-playhead]");
  if (playhead) playhead.style.left = `${editorPercent(time)}%`;
  const field = document.querySelector(`[data-editor-field="segment-${edge}"]`);
  if (field) field.value = time.toFixed(2);
  refreshEditorPlayhead();
});

function finishEditorDrag() {
  const editor = state.clipEditor;
  if (!editor?.drag) return;
  const before = editor.drag.before;
  editor.drag = null;
  if (JSON.stringify(before) !== JSON.stringify(editor.configuration)) {
    editor.history.push(before);
    editor.history = editor.history.slice(-40);
    editor.future = [];
  }
  refreshClipEditor();
}

document.addEventListener("pointerup", finishEditorDrag);
document.addEventListener("pointercancel", finishEditorDrag);

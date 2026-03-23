/* HydrogenX — Assistant Dashboard JS */
(() => {
  const dashboard = document.getElementById("dashboard-app");
  if (!dashboard) return;

  const form                  = document.getElementById("task-form");
  const promptInput           = document.getElementById("prompt");
  const agentSelect           = document.getElementById("agent_id");
  const submitBtn             = document.getElementById("submit-btn");
  const refreshBtn            = document.getElementById("refresh-btn");
  const taskList              = document.getElementById("task-list");
  const taskCount             = document.getElementById("task-count");
  const queueSummary          = document.getElementById("queue-summary");
  const uploadInput           = document.getElementById("upload-input");
  const uploadBtn             = document.getElementById("upload-btn");
  const uploadListEl          = document.getElementById("upload-list");
  const uploadFeedback        = document.getElementById("upload-feedback");
  const clearSelectedUploads  = document.getElementById("clear-selected-uploads");
  const csrfToken             = document.querySelector('meta[name="csrf-token"]').getAttribute("content");

  let uploadsState = [];

  /* ── fetch helper ─────────────────────────────────────── */
  async function fetchJSON(url, options = {}) {
    const headers = {
      "Accept": "application/json",
      "X-CSRFToken": csrfToken,
      ...(options.headers || {})
    };
    if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(url, { credentials: "same-origin", headers, ...options });
    const rawText = await response.text();
    let data = {};
    try { data = rawText ? JSON.parse(rawText) : {}; } catch (_) {}
    if (!response.ok) {
      const fallback = response.status === 413
        ? "上传内容过大，请缩小文件体积后重试。"
        : `请求失败（HTTP ${response.status}）`;
      const serverText = rawText && !data.error
        ? rawText.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 180)
        : "";
      throw new Error(data.error || serverText || fallback);
    }
    return data;
  }

  /* ── utils ────────────────────────────────────────────── */
  function escHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function formatDate(value) {
    if (!value) return "-";
    return new Date(value).toLocaleString("zh-CN");
  }

  function formatBytes(bytes) {
    const size = Number(bytes || 0);
    if (!size) return "0 B";
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function statusBadgeClass(status) {
    if (status === "queued")    return "badge-queued";
    if (status === "running")   return "badge-running";
    if (status === "completed") return "badge-done";
    return "badge-error";
  }

  /* ── render artifacts ─────────────────────────────────── */
  function renderArtifacts(artifacts) {
    if (!artifacts || !artifacts.length) return "";
    return `
      <div style="margin-top:12px;">
        <div class="task-section-label">生成文件</div>
        <div class="artifact-list-dark">
          ${artifacts.map(a => {
            const iconClass = a.kind === "image" ? "bi-image"
              : a.kind === "archive" ? "bi-file-earmark-zip"
              : "bi-file-earmark-arrow-down";
            const meta = [a.mime_type || "application/octet-stream"];
            if (a.size_bytes) meta.push(`${a.size_bytes} bytes`);
            return `
              <a class="artifact-card-dark" href="/api/artifacts/${a.id}/download">
                <div class="artifact-icon"><i class="bi ${iconClass}"></i></div>
                <div>
                  <div class="artifact-name">${escHtml(a.filename)}</div>
                  <div class="artifact-meta">${escHtml(meta.join(" · "))}</div>
                </div>
                <span class="artifact-dl">下载</span>
              </a>`;
          }).join("")}
        </div>
      </div>`;
  }

  /* ── render task uploads ──────────────────────────────── */
  function renderTaskUploads(uploads) {
    if (!uploads || !uploads.length) return "";
    return `
      <div style="margin-top:12px;">
        <div class="task-section-label">附带文件</div>
        <div class="task-upload-tags-dark">
          ${uploads.map(item => {
            const u = item.upload || {};
            return `<span class="task-upload-tag-dark">
              <i class="bi bi-paperclip"></i>
              ${escHtml(u.filename || `文件 #${item.upload_id}`)}
            </span>`;
          }).join("")}
        </div>
      </div>`;
  }

  /* ── render single task ───────────────────────────────── */
  function renderTask(task) {
    const responseHTML = task.response_text
      ? `<pre class="code-block-dark">${escHtml(task.response_text)}</pre>`
      : task.error_message
        ? `<pre class="code-block-dark is-error">${escHtml(task.error_message)}</pre>`
        : `<div class="placeholder-dark">等待结果中…</div>`;

    const rawHTML = task.raw_response_json
      ? `<details class="raw-details-dark" style="margin-top:12px;">
           <summary>查看原始响应 JSON</summary>
           <pre class="code-block-dark" style="margin-top:8px;">${escHtml(task.raw_response_json)}</pre>
         </details>`
      : "";

    return `
      <article class="task-card-dark" data-task-id="${task.id}">
        <div class="task-card-header">
          <div class="task-badges">
            <span class="badge-dark badge-id">#${task.id}</span>
            <span class="badge-dark ${statusBadgeClass(task.status)}">${escHtml(task.status)}</span>
            <span class="badge-dark badge-agent">${escHtml(task.agent_id)}</span>
          </div>
          <span class="task-date">${formatDate(task.created_at)}</span>
        </div>
        <div style="margin-top:12px;">
          <div class="task-section-label">Prompt</div>
          <pre class="code-block-dark">${escHtml(task.prompt)}</pre>
        </div>
        ${renderTaskUploads(task.uploads || [])}
        <div style="margin-top:12px;">
          <div class="task-section-label">Response</div>
          ${responseHTML}
        </div>
        ${renderArtifacts(task.artifacts || [])}
        ${rawHTML}
      </article>`;
  }

  /* ── render upload item ───────────────────────────────── */
  function renderUploadItem(upload) {
    const checked  = upload.selected ? "checked" : "";
    const disabled = upload.extraction_status !== "ready" ? "disabled" : "";
    const statusText = upload.extraction_status === "ready"
      ? `已提取 ${upload.extracted_chars || 0} 字`
      : (upload.extraction_error || upload.extraction_status || "不可用");
    const isReady = upload.extraction_status === "ready";

    return `
      <label class="upload-item-dark ${upload.selected ? "is-selected" : ""} ${!isReady ? "is-disabled" : ""}">
        <input class="upload-checkbox" type="checkbox" value="${upload.id}" ${checked} ${disabled}>
        <div class="upload-item-body">
          <div style="display:flex;justify-content:space-between;align-items:start;gap:8px;">
            <a class="upload-item-name" href="/api/uploads/${upload.id}/download">${escHtml(upload.filename)}</a>
            <button class="btn-del-upload upload-delete-btn" type="button" data-upload-id="${upload.id}" title="移除">
              <i class="bi bi-x"></i>
            </button>
          </div>
          <div class="upload-item-meta">${escHtml(upload.mime_type || "application/octet-stream")} · ${formatBytes(upload.size_bytes)}</div>
          <div class="upload-item-status ${isReady ? "is-ready" : "is-error"}">${escHtml(statusText)}</div>
        </div>
      </label>`;
  }

  function renderUploads() {
    if (!uploadsState.length) {
      uploadListEl.innerHTML = `<div class="placeholder-dark">还没有上传文件。</div>`;
      return;
    }
    uploadListEl.innerHTML = uploadsState.map(renderUploadItem).join("");
  }

  function selectedUploadIds() {
    return uploadsState
      .filter(item => item.selected && item.extraction_status === "ready")
      .map(item => item.id);
  }

  /* ── API calls ────────────────────────────────────────── */
  async function loadTasks() {
    try {
      const tasks = await fetchJSON("/api/tasks", { method: "GET" });
      taskCount.textContent = `${tasks.length} 条`;
      if (!tasks.length) {
        taskList.innerHTML = `
          <div class="empty-state-dark" id="empty-state">
            <i class="bi bi-chat-square-text"></i>
            <div class="es-title">还没有任务</div>
            <div class="es-sub">提交第一条请求后，这里会出现历史记录与处理状态。</div>
          </div>`;
        return;
      }
      taskList.innerHTML = tasks.map(renderTask).join("");
    } catch (e) {
      console.error("loadTasks:", e);
    }
  }

  async function loadQueue() {
    try {
      const queue = await fetchJSON("/api/system/queue", { method: "GET" });
      queueSummary.textContent = `运行中 ${queue.running_total} / ${queue.max_concurrent}，排队中 ${queue.queued_total}`;
    } catch (e) {
      queueSummary.textContent = "获取失败";
    }
  }

  async function loadUploads() {
    try {
      const uploads = await fetchJSON("/api/uploads", { method: "GET" });
      const currentSelection = new Map(uploadsState.map(item => [item.id, !!item.selected]));
      uploadsState = uploads.map(upload => ({
        ...upload,
        selected: upload.extraction_status === "ready"
          ? (currentSelection.get(upload.id) ?? false)
          : false
      }));
      renderUploads();
    } catch (e) {
      console.error("loadUploads:", e);
    }
  }

  async function uploadSelectedFiles() {
    const files = Array.from(uploadInput.files || []);
    if (!files.length) { window.alert("请先选择要上传的文件。"); return; }

    const formData = new FormData();
    formData.append("csrf_token", csrfToken);
    files.forEach(f => formData.append("files", f));

    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> 上传中...';
    uploadFeedback.textContent = `正在上传 ${files.length} 个文件...`;

    try {
      const result = await fetchJSON("/api/uploads", { method: "POST", body: formData, headers: {} });
      const newIds = new Set((result.uploads || []).map(item => item.id));
      uploadFeedback.textContent = result.message || "文件上传成功。";
      uploadInput.value = "";
      await loadUploads();
      uploadsState = uploadsState.map(item => ({
        ...item,
        selected: newIds.has(item.id) ? true : item.selected
      }));
      renderUploads();
    } catch (error) {
      uploadFeedback.textContent = error.message;
      window.alert(error.message);
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.innerHTML = '<i class="bi bi-upload"></i> 上传文件';
    }
  }

  async function deleteUpload(uploadId) {
    await fetchJSON(`/api/uploads/${uploadId}`, { method: "DELETE" });
    uploadsState = uploadsState.filter(item => item.id !== uploadId);
    renderUploads();
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const prompt  = promptInput.value.trim();
    const agentId = agentSelect.value;
    const uploadIds = selectedUploadIds();

    if (!prompt) { window.alert("请输入 prompt。"); return; }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i><span class="btn-label">提交中...</span>';

    try {
      await fetchJSON("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ prompt, agent_id: agentId, upload_ids: uploadIds })
      });
      promptInput.value = "";
      await loadTasks();
      await loadQueue();
    } catch (error) {
      window.alert(error.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '<i class="bi bi-send"></i><span class="btn-label">提交任务</span>';
    }
  }

  /* ── Event listeners ──────────────────────────────────── */
  form.addEventListener("submit", handleSubmit);

  refreshBtn.addEventListener("click", async () => {
    refreshBtn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> 刷新';
    await Promise.all([loadTasks(), loadQueue(), loadUploads()]);
    refreshBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> 刷新';
  });

  uploadBtn.addEventListener("click", uploadSelectedFiles);

  uploadListEl.addEventListener("change", event => {
    const checkbox = event.target.closest(".upload-checkbox");
    if (!checkbox) return;
    const uploadId = Number(checkbox.value);
    uploadsState = uploadsState.map(item =>
      item.id === uploadId ? { ...item, selected: checkbox.checked } : item
    );
    renderUploads();
  });

  uploadListEl.addEventListener("click", async event => {
    const button = event.target.closest(".upload-delete-btn");
    if (!button) return;
    const uploadId = Number(button.getAttribute("data-upload-id"));
    try {
      await deleteUpload(uploadId);
      uploadFeedback.textContent = "文件已移除。";
    } catch (error) {
      window.alert(error.message);
    }
  });

  clearSelectedUploads.addEventListener("click", () => {
    uploadsState = uploadsState.map(item => ({ ...item, selected: false }));
    renderUploads();
  });

  /* ── Init ─────────────────────────────────────────────── */
  loadTasks();
  loadQueue();
  loadUploads();
  window.setInterval(loadTasks, 3000);
  window.setInterval(loadQueue, 5000);
})();

/* ── Global spin animation ────────────────────────────── */
const spinStyle = document.createElement("style");
spinStyle.textContent = `.spin { animation: _spin 1s linear infinite; display:inline-block; }
@keyframes _spin { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }`;
document.head.appendChild(spinStyle);

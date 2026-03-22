(() => {
  const dashboard = document.getElementById("dashboard-app");
  if (!dashboard) return;

  const form = document.getElementById("task-form");
  const promptInput = document.getElementById("prompt");
  const agentSelect = document.getElementById("agent_id");
  const uploadInput = document.getElementById("upload-input");
  const uploadList = document.getElementById("upload-list");
  const submitBtn = document.getElementById("submit-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  const taskList = document.getElementById("task-list");
  const taskCount = document.getElementById("task-count");
  const queueSummary = document.getElementById("queue-summary");
  const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute("content");
  let pendingUploads = [];

  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {
      headers: {
        "X-CSRFToken": csrfToken,
        ...(options.headers || {})
      },
      ...options
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "请求失败");
    }
    return data;
  }

  function statusBadgeClass(status) {
    if (status === "queued") return "text-bg-warning";
    if (status === "running") return "text-bg-primary";
    if (status === "completed") return "text-bg-success";
    return "text-bg-danger";
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    return date.toLocaleString("zh-CN");
  }

  function renderDownloadCards(items, routePrefix, iconClass = "bi-file-earmark-arrow-down", title = "Files") {
    if (!items || !items.length) return "";
    return `
      <div class="mt-3">
        <div class="small text-secondary mb-2">${escapeHtml(title)}</div>
        <div class="artifact-list">
          ${items.map((item) => {
            const meta = [item.mime_type || "application/octet-stream"];
            if (item.size_bytes) meta.push(`${item.size_bytes} bytes`);
            return `
              <a class="artifact-card" href="${routePrefix}/${item.id}/download">
                <div class="artifact-card__icon"><i class="bi ${iconClass}"></i></div>
                <div class="artifact-card__body">
                  <div class="artifact-card__name">${escapeHtml(item.filename)}</div>
                  <div class="artifact-card__meta">${escapeHtml(meta.join(" · "))}</div>
                </div>
                <span class="artifact-card__action">下载</span>
              </a>
            `;
          }).join("")}
        </div>
      </div>
    `;
  }

  function renderArtifacts(artifacts) {
    if (!artifacts || !artifacts.length) return "";
    return `
      <div class="mt-3">
        <div class="small text-secondary mb-2">Generated Files</div>
        <div class="artifact-list">
          ${artifacts.map((artifact) => {
            const iconClass = artifact.kind === "image"
              ? "bi-image"
              : artifact.kind === "archive"
                ? "bi-file-earmark-zip"
                : "bi-file-earmark-arrow-down";
            const meta = [artifact.mime_type || "application/octet-stream"];
            if (artifact.size_bytes) meta.push(`${artifact.size_bytes} bytes`);
            return `
              <a class="artifact-card" href="/api/artifacts/${artifact.id}/download">
                <div class="artifact-card__icon"><i class="bi ${iconClass}"></i></div>
                <div class="artifact-card__body">
                  <div class="artifact-card__name">${escapeHtml(artifact.filename)}</div>
                  <div class="artifact-card__meta">${escapeHtml(meta.join(" · "))}</div>
                </div>
                <span class="artifact-card__action">下载</span>
              </a>
            `;
          }).join("")}
        </div>
      </div>
    `;
  }

  function renderPendingUploads() {
    if (!pendingUploads.length) {
      uploadList.innerHTML = "";
      return;
    }
    uploadList.innerHTML = `
      <div class="small text-secondary mb-2">Pending Uploads</div>
      <div class="artifact-list">
        ${pendingUploads.map((upload) => `
          <div class="artifact-card">
            <div class="artifact-card__icon"><i class="bi bi-paperclip"></i></div>
            <div class="artifact-card__body">
              <div class="artifact-card__name">${escapeHtml(upload.filename)}</div>
              <div class="artifact-card__meta">${escapeHtml((upload.mime_type || "application/octet-stream") + (upload.size_bytes ? ` · ${upload.size_bytes} bytes` : ""))}</div>
            </div>
            <button class="btn btn-sm btn-outline-secondary remove-upload-btn" type="button" data-upload-id="${upload.id}">移除</button>
          </div>
        `).join("")}
      </div>
    `;
    uploadList.querySelectorAll(".remove-upload-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.getAttribute("data-upload-id"));
        pendingUploads = pendingUploads.filter((item) => item.id !== id);
        renderPendingUploads();
      });
    });
  }

  function renderTask(task) {
    const responseHTML = task.response_text
      ? `<pre class="code-block response">${escapeHtml(task.response_text)}</pre>`
      : task.error_message
        ? `<pre class="code-block response error">${escapeHtml(task.error_message)}</pre>`
        : `<div class="placeholder-block">等待结果中…</div>`;

    const hintHTML = !task.artifacts?.length && task.status === "completed" && task.response_text &&
      (task.response_text.includes("创建成功") || task.response_text.includes("文件") || task.response_text.includes("Message failed"))
      ? `<div class="mt-3"><div class="hint-block">本次回复提到了文件，但未解析出可下载产物。当前版本会优先解析 OpenClaw artifact 协议，并对这类“小文本文件或简单 zip 打包”请求做自动重建；重新发送同类请求后，应出现下载链接。</div></div>`
      : "";

    const rawHTML = task.raw_response_json
      ? `<details class="mt-3 raw-details"><summary>查看原始响应 JSON</summary><pre class="code-block response">${escapeHtml(task.raw_response_json)}</pre></details>`
      : "";

    return `
      <article class="task-card" data-task-id="${task.id}">
        <div class="task-card__header">
          <div>
            <div class="d-flex align-items-center gap-2 flex-wrap">
              <span class="badge text-bg-dark">#${task.id}</span>
              <span class="badge rounded-pill ${statusBadgeClass(task.status)}">${escapeHtml(task.status)}</span>
              <span class="small text-secondary">agent: ${escapeHtml(task.agent_id)}</span>
            </div>
          </div>
          <div class="small text-secondary">${formatDate(task.created_at)}</div>
        </div>

        <div class="mt-3">
          <div class="small text-secondary mb-1">Prompt</div>
          <pre class="code-block">${escapeHtml(task.prompt)}</pre>
        </div>

        ${renderDownloadCards(task.uploads || [], "/api/uploads", "bi-paperclip", "Uploaded Files")}

        <div class="mt-3">
          <div class="small text-secondary mb-1">Response</div>
          ${responseHTML}
        </div>

        ${renderArtifacts(task.artifacts || [])}
        ${hintHTML}
        ${rawHTML}
      </article>
    `;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  async function loadTasks() {
    const tasks = await fetchJSON("/api/tasks", { method: "GET" });
    taskCount.textContent = `${tasks.length} 条`;

    if (!tasks.length) {
      taskList.innerHTML = `
        <div class="empty-state" id="empty-state">
          <i class="bi bi-chat-square-text"></i>
          <div class="fw-semibold mt-2">还没有任务</div>
          <div class="text-secondary small">提交第一条请求后，这里会出现历史记录与处理状态。</div>
        </div>
      `;
      return;
    }

    taskList.innerHTML = tasks.map(renderTask).join("");
  }

  async function loadQueue() {
    const queue = await fetchJSON("/api/system/queue", { method: "GET" });
    queueSummary.textContent = `运行中 ${queue.running_total} / ${queue.max_concurrent}，排队中 ${queue.queued_total}`;
  }

  async function uploadSingleFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    return fetchJSON("/api/uploads", {
      method: "POST",
      body: formData,
      headers: {}
    });
  }

  async function createTask(prompt, agentId, uploadIds) {
    return fetchJSON("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        agent_id: agentId,
        upload_ids: uploadIds
      })
    });
  }

  async function handleUploadChange(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;

    uploadInput.disabled = true;
    try {
      for (const file of files) {
        const result = await uploadSingleFile(file);
        pendingUploads.unshift(result.upload);
      }
      renderPendingUploads();
      uploadInput.value = "";
    } catch (error) {
      window.alert(error.message);
    } finally {
      uploadInput.disabled = false;
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const prompt = promptInput.value.trim();
    const agentId = agentSelect.value;

    if (!prompt) {
      window.alert("请输入 prompt。");
      return;
    }

    submitBtn.disabled = true;
    submitBtn.querySelector(".btn-label").textContent = "提交中...";

    try {
      await createTask(prompt, agentId, pendingUploads.map((item) => item.id));
      promptInput.value = "";
      pendingUploads = [];
      renderPendingUploads();
      await loadTasks();
      await loadQueue();
    } catch (error) {
      window.alert(error.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.querySelector(".btn-label").textContent = "提交任务";
    }
  }

  form.addEventListener("submit", handleSubmit);
  if (uploadInput) {
    uploadInput.addEventListener("change", handleUploadChange);
  }
  refreshBtn.addEventListener("click", async () => {
    await loadTasks();
    await loadQueue();
  });

  loadTasks();
  loadQueue();
  renderPendingUploads();
  window.setInterval(loadTasks, 3000);
  window.setInterval(loadQueue, 5000);
})();

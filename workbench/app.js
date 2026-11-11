"use strict";

/* ════════════════════════════════════════════════════════════════
 * WorkLoom 最小前端工作台
 *
 * 设计目标：不引入构建工具，用静态文件直接把双服务串起来。
 *   - TaskFlow :8000 → 登录、任务 CRUD、相关知识、生成任务预览/确认
 *   - DocMind  :8001 → 文档上传、列表、片段原文
 * 两个服务通过同一个 JWT 识别用户；浏览器跨域调用依赖后端的 CORS 配置。
 * ════════════════════════════════════════════════════════════════ */

const STORAGE_KEYS = {
  token: "workloom.token",
  username: "workloom.username",
  taskflowApi: "workloom.taskflowApi",
  docmindApi: "workloom.docmindApi",
};

const state = {
  token: localStorage.getItem(STORAGE_KEYS.token) || "",
  username: localStorage.getItem(STORAGE_KEYS.username) || "",
  taskflowApi:
    localStorage.getItem(STORAGE_KEYS.taskflowApi) || "http://localhost:8000",
  docmindApi:
    localStorage.getItem(STORAGE_KEYS.docmindApi) || "http://localhost:8001",
  tasks: [],
  documents: [],
  selectedTaskId: null,
};

let detailSeq = 0;
let toastTimer = null;

/* ── DOM 小工具 ─────────────────────────────────────────────── */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3200);
}

/* ── API 封装 ───────────────────────────────────────────────── */
async function api(service, path, { method = "GET", body, formData } = {}) {
  const base = service === "docmind" ? state.docmindApi : state.taskflowApi;
  const headers = {};
  if (state.token) {
    headers.Authorization = `Bearer ${state.token}`;
  }

  let payload;
  if (formData) {
    payload = formData;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const response = await fetch(`${base}${path}`, {
    method,
    headers,
    body: payload,
  });

  if (response.status === 401) {
    logout("登录已过期，请重新登录");
    throw new Error("unauthorized");
  }

  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = {};
    }
  }

  if (!response.ok) {
    const message =
      data?.error?.message || data?.detail || `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return data;
}

/* ── 弹窗 ───────────────────────────────────────────────────── */
function openModal(contentHtml, { wide = false } = {}) {
  const root = $("#modal-root");
  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal ${wide ? "wide" : ""}">
        <button class="modal-close" type="button" aria-label="关闭">×</button>
        <div class="modal-content">${contentHtml}</div>
      </div>
    </div>`;

  const backdrop = $(".modal-backdrop", root);
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  const close = () => {
    root.innerHTML = "";
    document.removeEventListener("keydown", onKey);
  };

  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  $(".modal-close", root).addEventListener("click", close);
  document.addEventListener("keydown", onKey);

  return { root, close };
}

/* ── 登录 / 登出 ────────────────────────────────────────────── */
function showLogin() {
  $("#login-view").classList.remove("hidden");
  $("#main-view").classList.add("hidden");
  $("#logout-btn").classList.add("hidden");
  $("#current-user").textContent = "";
}

function showMain() {
  $("#login-view").classList.add("hidden");
  $("#main-view").classList.remove("hidden");
  $("#logout-btn").classList.remove("hidden");
  $("#current-user").textContent = state.username
    ? `已登录：${state.username}`
    : "";
}

function logout(message) {
  state.token = "";
  state.username = "";
  state.tasks = [];
  state.documents = [];
  state.selectedTaskId = null;
  localStorage.removeItem(STORAGE_KEYS.token);
  localStorage.removeItem(STORAGE_KEYS.username);
  showLogin();
  if (message) toast(message, true);
}

async function authenticate(path, username, password) {
  const response = await fetch(`${state.taskflowApi}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      data?.error?.message || data?.detail || `请求失败 (${response.status})`
    );
  }
  return data;
}

async function login(username, password) {
  const data = await authenticate("/login", username, password);
  state.token = data.access_token;
  state.username = username;
  localStorage.setItem(STORAGE_KEYS.token, state.token);
  localStorage.setItem(STORAGE_KEYS.username, username);

  showMain();
  await Promise.all([loadTasks(), loadDocuments()]);
  checkHealth();
  toast(`欢迎，${username}`);
}

/* ── 服务健康状态 ───────────────────────────────────────────── */
async function checkService(dotSelector, base) {
  const dot = $(dotSelector);
  try {
    const response = await fetch(`${base}/health`);
    dot.className = `dot ${response.ok ? "ok" : "bad"}`;
  } catch {
    dot.className = "dot bad";
  }
}

function checkHealth() {
  checkService("#taskflow-dot", state.taskflowApi);
  checkService("#docmind-dot", state.docmindApi);
}

/* ── 任务列表 ───────────────────────────────────────────────── */
async function loadTasks() {
  const data = await api("taskflow", "/tasks?page=1&page_size=100");
  state.tasks = data.tasks || [];
  renderTasks();
}

function renderTasks() {
  const list = $("#task-list");
  if (!state.tasks.length) {
    list.innerHTML = `<p class="empty-state">还没有任务，先添加一个吧。</p>`;
    return;
  }
  list.innerHTML = state.tasks
    .map(
      (task) => `
      <button class="list-item ${task.id === state.selectedTaskId ? "active" : ""}"
              type="button" data-task-id="${task.id}">
        <span class="item-title ${task.done ? "done" : ""}">${esc(task.title)}</span>
        <span class="item-meta">
          <span class="chip ${esc(task.priority)}">${esc(task.priority)}</span>
          ${task.deadline ? `<span class="chip">${esc(task.deadline)}</span>` : ""}
          ${task.source_filename ? `<span class="chip citation">引用</span>` : ""}
        </span>
      </button>`
    )
    .join("");
}

async function createTask(title) {
  const task = await api("taskflow", "/tasks", {
    method: "POST",
    body: { title },
  });
  await loadTasks();
  openTask(task.id);
}

/* ── 任务详情 ───────────────────────────────────────────────── */
async function openTask(taskId) {
  state.selectedTaskId = taskId;
  renderTasks();

  const task = state.tasks.find((item) => item.id === taskId);
  if (!task) return;

  const sequence = ++detailSeq;
  const panel = $("#task-detail");
  panel.innerHTML = `<p class="muted">加载中…</p>`;

  const [linksResult, knowledgeResult] = await Promise.allSettled([
    api("taskflow", `/tasks/${taskId}/documents`),
    api("taskflow", `/tasks/${taskId}/knowledge?top_k=5`),
  ]);
  if (sequence !== detailSeq) return;

  const links =
    linksResult.status === "fulfilled" ? linksResult.value.items || [] : [];
  const knowledge =
    knowledgeResult.status === "fulfilled"
      ? knowledgeResult.value.results || []
      : [];
  const knowledgeError =
    knowledgeResult.status === "rejected"
      ? knowledgeResult.reason?.message || "检索失败"
      : null;

  renderTaskDetail(task, links, knowledge, knowledgeError);
}

function invalidationText(reason) {
  if (reason === "document_deleted") return "来源文档已删除";
  if (reason === "document_reindexed") {
    return "文档已重建索引，引用可能失效，请重新生成或重新关联";
  }
  if (reason === "document_not_found") return "来源文档已不存在";
  return "引用已失效";
}

function knowledgeHtml(results) {
  if (!results.length) {
    return `<p class="muted">没有找到相关片段。可以给任务关联文档，或先上传知识库文档。</p>`;
  }
  return results
    .map(
      (item) => `
      <div class="knowledge-item"
           data-doc="${item.document_id}"
           data-chunk="${item.chunk_index}"
           data-file="${esc(item.filename || "")}">
        <div class="knowledge-snippet">${esc(item.content)}</div>
        <div class="knowledge-meta">
          <span class="chip">${esc(item.filename || `文档 ${item.document_id}`)}</span>
          <span class="chip">#${item.chunk_index}</span>
          ${item.page != null ? `<span class="chip">第 ${item.page} 页</span>` : ""}
          <span class="chip">${Number(item.score).toFixed(3)}</span>
        </div>
      </div>`
    )
    .join("");
}

function bindKnowledgeItems(container) {
  if (!container) return;
  container.addEventListener("click", (event) => {
    const item = event.target.closest(".knowledge-item");
    if (!item) return;
    openChunk(
      Number(item.dataset.doc),
      Number(item.dataset.chunk),
      item.dataset.file
    );
  });
}

async function loadKnowledge(taskId, linkedOnly) {
  const container = $("#knowledge-results");
  if (!container) return;
  container.innerHTML = `<p class="muted">检索中…</p>`;
  try {
    const query = linkedOnly ? "true" : "false";
    const data = await api(
      "taskflow",
      `/tasks/${taskId}/knowledge?top_k=5&linked_only=${query}`
    );
    container.innerHTML = knowledgeHtml(data.results || []);
  } catch (error) {
    container.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

function renderTaskDetail(task, links, knowledge, knowledgeError) {
  const panel = $("#task-detail");
  const linkedIds = new Set(links.map((link) => link.document_id));
  const availableDocuments = state.documents.filter(
    (document) => !linkedIds.has(document.id)
  );

  panel.innerHTML = `
    <div class="detail-title">
      <h3 class="${task.done ? "item-title done" : ""}">${esc(task.title)}</h3>
      <div class="row">
        <button class="ghost small" id="toggle-done" type="button">
          ${task.done ? "标记未完成" : "标记完成"}
        </button>
        <button class="danger small" id="delete-task" type="button">删除</button>
      </div>
    </div>

    <div class="meta-row">
      <span class="chip ${esc(task.priority)}">${esc(task.priority)}</span>
      ${task.deadline
        ? `<span class="chip">截止 ${esc(task.deadline)}</span>`
        : `<span class="chip">无截止日期</span>`}
      <span class="chip">${task.done ? "已完成" : "进行中"}</span>
    </div>

    <div class="detail-section">
      <h4>描述</h4>
      <div class="detail-desc">${
        task.description
          ? esc(task.description)
          : `<span class="muted">没有描述</span>`
      }</div>
    </div>

    ${
      task.source_document_id != null
        ? `<div class="detail-section">
             <h4>来源引用</h4>
             <div class="citation-box">
               <div>
                 <strong>${esc(task.source_filename || `文档 ${task.source_document_id}`)}</strong>
                 <div class="muted">片段 #${task.source_chunk_index ?? "-"}</div>
                 ${
                   task.source_invalidated_at
                     ? `<div class="error">${esc(invalidationText(task.source_invalid_reason))}</div>`
                     : ""
                 }
               </div>
               <button class="ghost small" id="view-citation" type="button"
                       ${task.source_chunk_index == null || task.source_invalid_reason === "document_deleted" ? "disabled" : ""}>
                 查看原文
               </button>
             </div>
           </div>`
        : ""
    }

    <div class="detail-section">
      <h4>关联文档</h4>
      <div class="linked-list">
        ${
          links.length
            ? links
                .map(
                  (link) => `
              <span class="linked-chip" ${link.invalidated_at ? `title="${esc(invalidationText(link.invalid_reason))}"` : ""}>
                ${esc(link.filename || `文档 ${link.document_id}`)}
                ${link.invalidated_at ? `<span class="chip citation">已失效</span>` : ""}
                <button type="button" data-unlink-doc="${link.document_id}"
                        title="解除关联">×</button>
              </span>`
                )
                .join("")
            : `<span class="muted">还没有关联文档。</span>`
        }
      </div>
      <div class="row" style="margin-top:8px">
        <select id="link-doc-select">
          <option value="">选择知识库文档…</option>
          ${availableDocuments
            .map(
              (document) => `
            <option value="${document.id}"
                    ${document.status !== "completed" ? "disabled" : ""}>
              ${esc(document.filename)}${document.status !== "completed" ? "（索引中）" : ""}
            </option>`
            )
            .join("")}
        </select>
        <button class="small" id="link-doc-btn" type="button">关联</button>
      </div>
    </div>

    <div class="detail-section">
      <h4 style="display:flex;justify-content:space-between;align-items:center">
        <span>相关知识</span>
        <label class="muted"
               style="text-transform:none;letter-spacing:0;font-size:12px">
          <input type="checkbox" id="linked-only" /> 只看关联文档
        </label>
      </h4>
      <div id="knowledge-results">
        ${
          knowledgeError
            ? `<p class="error">${esc(knowledgeError)}</p>`
            : knowledgeHtml(knowledge)
        }
      </div>
    </div>`;

  $("#toggle-done", panel).addEventListener("click", async () => {
    try {
      await api("taskflow", `/tasks/${task.id}`, {
        method: "PATCH",
        body: { done: !task.done },
      });
      await loadTasks();
      openTask(task.id);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#delete-task", panel).addEventListener("click", async () => {
    if (!window.confirm(`删除任务「${task.title}」？`)) return;
    try {
      await api("taskflow", `/tasks/${task.id}`, { method: "DELETE" });
      state.selectedTaskId = null;
      panel.innerHTML = `<p class="empty-state">任务已删除。选择左侧任务继续。</p>`;
      await loadTasks();
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#view-citation", panel)?.addEventListener("click", () => {
    openChunk(
      task.source_document_id,
      task.source_chunk_index,
      task.source_filename
    );
  });

  $$("[data-unlink-doc]", panel).forEach((button) => {
    button.addEventListener("click", async () => {
      const documentId = Number(button.dataset.unlinkDoc);
      try {
        await api(
          "taskflow",
          `/tasks/${task.id}/documents/${documentId}`,
          { method: "DELETE" }
        );
        await openTask(task.id);
      } catch (error) {
        toast(error.message, true);
      }
    });
  });

  $("#link-doc-btn", panel).addEventListener("click", async () => {
    const select = $("#link-doc-select", panel);
    const documentId = Number(select.value);
    if (!documentId) {
      toast("先选择一个文档", true);
      return;
    }
    try {
      await api("taskflow", `/tasks/${task.id}/documents`, {
        method: "POST",
        body: { document_id: documentId },
      });
      await openTask(task.id);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#linked-only", panel).addEventListener("change", (event) => {
    loadKnowledge(task.id, event.target.checked);
  });

  bindKnowledgeItems($("#knowledge-results", panel));
}

/* ── 文档列表 ───────────────────────────────────────────────── */
async function loadDocuments() {
  const data = await api("docmind", "/documents?skip=0&limit=100");
  state.documents = data.items || [];
  renderDocuments();
}

function renderDocuments() {
  const list = $("#document-list");
  if (!state.documents.length) {
    list.innerHTML = `<p class="empty-state">还没有文档，上传一份试试。</p>`;
    return;
  }
  list.innerHTML = state.documents
    .map(
      (document) => `
      <div class="row-item">
        <div class="row-item-head">
          <span class="item-title">${esc(document.filename)}</span>
          <span class="chip status-${esc(document.status)}">${esc(document.status)}</span>
        </div>
        <div class="muted">${document.chunk_count ?? 0} 个片段</div>
        <div class="row-item-actions">
          <button class="ghost small" data-action="chunks" data-id="${document.id}" type="button">浏览片段</button>
          <button class="small" data-action="generate" data-id="${document.id}" type="button"
                  ${document.status !== "completed" ? "disabled" : ""}>生成任务</button>
          <button class="danger small" data-action="delete" data-id="${document.id}" type="button">删除</button>
        </div>
      </div>`
    )
    .join("");
}

async function pollDocument(documentId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const document = await api("docmind", `/documents/${documentId}`);
    if (document.status === "completed" || document.status === "failed") {
      return document;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  return null;
}

async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);
  const document = await api("docmind", "/documents/upload", {
    method: "POST",
    formData,
  });
  toast(`已上传 ${document.filename}，正在索引…`);
  await pollDocument(document.id);
  await loadDocuments();
}

/* ── 片段原文 ───────────────────────────────────────────────── */
async function openChunk(documentId, chunkIndex, filename) {
  try {
    const chunk = await api(
      "docmind",
      `/documents/${documentId}/chunks/${chunkIndex}`
    );
    openModal(
      `
      <h3>引用原文</h3>
      <div class="row">
        <span class="chip">${esc(filename || `文档 ${documentId}`)}</span>
        <span class="chip">片段 #${chunk.chunk_index}</span>
        ${chunk.page != null ? `<span class="chip">第 ${chunk.page} 页</span>` : ""}
      </div>
      <div class="chunk" style="margin-top:12px">
        <pre>${esc(chunk.content)}</pre>
      </div>`,
      { wide: true }
    );
  } catch (error) {
    toast(error.message, true);
  }
}

async function openDocumentReader(document) {
  const modal = openModal(
    `<h3>${esc(document.filename)} · 片段</h3><div id="chunk-view"></div>`,
    { wide: true }
  );
  const limit = 10;
  let skip = 0;

  async function render() {
    const view = $("#chunk-view", modal.root);
    view.innerHTML = `<p class="muted">加载中…</p>`;
    try {
      const data = await api(
        "docmind",
        `/documents/${document.id}/chunks?skip=${skip}&limit=${limit}`
      );
      const items = data.items || [];
      view.innerHTML = `
        <div class="row">
          <span class="muted">共 ${data.total} 个片段</span>
          <button class="ghost small" id="chunk-prev" type="button"
                  ${skip === 0 ? "disabled" : ""}>上一页</button>
          <button class="ghost small" id="chunk-next" type="button"
                  ${skip + limit >= data.total ? "disabled" : ""}>下一页</button>
        </div>
        <div class="chunk-list">
          ${
            items.length
              ? items
                  .map(
                    (item) => `
                <div class="chunk">
                  <div class="chunk-head">
                    <span class="chip">#${item.chunk_index}</span>
                    ${item.page != null ? `<span class="chip">第 ${item.page} 页</span>` : ""}
                  </div>
                  <pre>${esc(item.content)}</pre>
                </div>`
                  )
                  .join("")
              : `<p class="empty-state">没有片段</p>`
          }
        </div>`;

      $("#chunk-prev", view).addEventListener("click", () => {
        skip = Math.max(0, skip - limit);
        render();
      });
      $("#chunk-next", view).addEventListener("click", () => {
        skip += limit;
        render();
      });
    } catch (error) {
      view.innerHTML = `<p class="error">${esc(error.message)}</p>`;
    }
  }

  await render();
}

/* ── 生成任务：预览 → 确认 ──────────────────────────────────── */
function openGenerateModal(document) {
  const modal = openModal(
    `
    <h3>从「${esc(document.filename)}」生成任务</h3>
    <div class="row">
      <input id="gen-instruction"
             placeholder="额外要求（可选），例如：只提取测试相关任务" />
      <label class="muted">最多
        <input id="gen-max" type="number" min="1" max="10" value="5" />
      </label>
      <button id="gen-preview-btn" type="button">生成预览</button>
    </div>
    <p class="muted">预览不会写入数据库；确认后才会创建任务。</p>
    <div id="gen-results"></div>
    <div class="row" id="gen-actions"></div>`,
    { wide: true }
  );

  const results = $("#gen-results", modal.root);
  const actions = $("#gen-actions", modal.root);

  $("#gen-preview-btn", modal.root).addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    results.innerHTML = `<p class="muted">生成中…</p>`;
    actions.innerHTML = "";
    try {
      const data = await api("taskflow", "/ai/tasks/from-document/preview", {
        method: "POST",
        body: {
          document_id: document.id,
          instruction:
            $("#gen-instruction", modal.root).value.trim() || null,
          max_tasks: Number($("#gen-max", modal.root).value) || 5,
        },
      });
      renderPreview(data.tasks || []);
    } catch (error) {
      results.innerHTML = `<p class="error">${esc(error.message)}</p>`;
    } finally {
      button.disabled = false;
    }
  });

  function renderPreview(tasks) {
    if (!tasks.length) {
      results.innerHTML = `<p class="empty-state">没有提取到可执行任务，可以换个要求再试。</p>`;
      return;
    }

    results.innerHTML = `
      <div class="preview-list">
        ${tasks
          .map(
            (task) => `
          <div class="preview-row" data-chunk-index="${task.source_chunk_index ?? ""}">
            <input type="checkbox" class="gen-check" checked />
            <div class="preview-body">
              <input class="gen-title" value="${esc(task.title)}" maxlength="50" />
              <div class="row">
                <select class="gen-priority">
                  ${["low", "medium", "high"]
                    .map(
                      (priority) => `
                    <option value="${priority}"
                            ${task.priority === priority ? "selected" : ""}>
                      ${priority}
                    </option>`
                    )
                    .join("")}
                </select>
                <input class="gen-deadline" type="date"
                       value="${esc(task.deadline || "")}" />
              </div>
              <textarea class="gen-description" maxlength="500" rows="2">${esc(task.description || "")}</textarea>
              ${
                task.source_snippet
                  ? `<details class="preview-snippet">
                       <summary>引用片段 #${task.source_chunk_index}</summary>
                       <pre>${esc(task.source_snippet)}</pre>
                     </details>`
                  : ""
              }
            </div>
          </div>`
          )
          .join("")}
      </div>`;

    actions.innerHTML = `<button id="gen-apply-btn" type="button">确认创建所选任务</button>`;
    $("#gen-apply-btn", actions).addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const rows = $$(".preview-row", results);
      const payload = rows
        .filter((row) => $(".gen-check", row).checked)
        .map((row) => {
          const chunkIndex = row.dataset.chunkIndex;
          return {
            title: $(".gen-title", row).value.trim(),
            description: $(".gen-description", row).value.trim() || null,
            priority: $(".gen-priority", row).value,
            deadline: $(".gen-deadline", row).value || null,
            source_chunk_index: chunkIndex === "" ? null : Number(chunkIndex),
          };
        });

      if (!payload.length) {
        toast("请至少选择一条任务", true);
        return;
      }
      if (payload.some((task) => !task.title)) {
        toast("任务标题不能为空", true);
        return;
      }

      button.disabled = true;
      try {
        const data = await api("taskflow", "/ai/tasks/from-document/apply", {
          method: "POST",
          body: { document_id: document.id, tasks: payload },
        });
        modal.close();
        toast(`已创建 ${data.tasks.length} 个任务`);
        await loadTasks();
        if (data.tasks.length) openTask(data.tasks[0].id);
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    });
  }
}

/* ── 事件绑定与启动 ─────────────────────────────────────────── */
function bindEvents() {
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#username").value.trim();
    const password = $("#password").value;
    if (!username || !password) {
      $("#login-error").textContent = "请输入用户名和密码";
      return;
    }
    try {
      await login(username, password);
      $("#login-error").textContent = "";
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  });

  $("#register-btn").addEventListener("click", async () => {
    const username = $("#username").value.trim();
    const password = $("#password").value;
    if (!username || password.length < 8) {
      $("#login-error").textContent = "用户名必填，密码至少 8 位";
      return;
    }
    try {
      await authenticate("/register", username, password);
      await login(username, password);
      $("#login-error").textContent = "";
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  });

  $("#logout-btn").addEventListener("click", () => logout("已退出登录"));

  $("#create-task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#new-task-title");
    const title = input.value.trim();
    if (!title) return;
    try {
      await createTask(title);
      input.value = "";
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#task-list").addEventListener("click", (event) => {
    const item = event.target.closest(".list-item");
    if (item) openTask(Number(item.dataset.taskId));
  });

  $("#upload-input").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = "";
    try {
      await uploadDocument(file);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#document-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const document = state.documents.find(
      (item) => item.id === Number(button.dataset.id)
    );
    if (!document) return;

    if (button.dataset.action === "chunks") {
      openDocumentReader(document);
      return;
    }
    if (button.dataset.action === "generate") {
      openGenerateModal(document);
      return;
    }
    if (button.dataset.action === "delete") {
      if (!window.confirm(`删除文档「${document.filename}」？任务的引用会失效。`)) {
        return;
      }
      try {
        await api("docmind", `/documents/${document.id}`, { method: "DELETE" });
        toast("文档已删除");
        await loadDocuments();
        if (state.selectedTaskId) openTask(state.selectedTaskId);
      } catch (error) {
        toast(error.message, true);
      }
    }
  });

  const onApiConfigChange = async () => {
    state.taskflowApi =
      $("#taskflow-api").value.trim().replace(/\/$/, "") ||
      "http://localhost:8000";
    state.docmindApi =
      $("#docmind-api").value.trim().replace(/\/$/, "") ||
      "http://localhost:8001";
    localStorage.setItem(STORAGE_KEYS.taskflowApi, state.taskflowApi);
    localStorage.setItem(STORAGE_KEYS.docmindApi, state.docmindApi);
    checkHealth();
    if (!state.token) return;
    try {
      await Promise.all([loadTasks(), loadDocuments()]);
    } catch (error) {
      if (error.message !== "unauthorized") toast(error.message, true);
    }
  };
  $("#taskflow-api").addEventListener("change", onApiConfigChange);
  $("#docmind-api").addEventListener("change", onApiConfigChange);
}

async function boot() {
  $("#taskflow-api").value = state.taskflowApi;
  $("#docmind-api").value = state.docmindApi;
  bindEvents();
  checkHealth();

  if (!state.token) {
    showLogin();
    return;
  }

  try {
    await Promise.all([loadTasks(), loadDocuments()]);
    showMain();
  } catch (error) {
    if (error.message !== "unauthorized") {
      toast(error.message, true);
      showLogin();
    }
  }
}

boot();

const $ = (id) => document.getElementById(id);

const STAGES = [
  ["READY", "准备"], ["GENERATING", "生成"], ["AUDITING", "审计"],
  ["PENDING_REVIEW", "人工审核"], ["APPROVED", "批准"],
  ["PENDING_PUBLICATION", "导出/发布"], ["PUBLISHED", "已发布"],
  ["EVALUATION_PASSED", "验收"]
];
const STATE_INDEX = {
  READY: 0, GENERATING: 1, GENERATED: 1, AUDITING: 2, REPAIRING: 2,
  PENDING_REVIEW: 3, APPROVED: 4, EXPORTED: 5, PENDING_PUBLICATION: 5,
  PUBLISHED: 6, EVALUATION_PASSED: 7, EVALUATION_FAILED: 7,
  RETURNED: 3, STOPPED: 2, INTERRUPTED: 2
};
const RUNNING = new Set(["READY", "GENERATING", "GENERATED", "AUDITING", "REPAIRING"]);
let bootstrap = null;
let activeId = "";
let activeDocument = null;
let pollTimer = null;
let selectionRequest = 0;
let checksRenderKey = "";
let renderedVersionKey = "";

const CARD_TYPES = {concept: "概念", fact: "事实", method: "方法", faq: "问答"};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})}
  });
  const value = await response.json();
  if (!response.ok || value.ok === false) throw new Error(value.error || `请求失败：${response.status}`);
  return value;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.hidden = true, 3500);
}

function profileById(id) { return bootstrap?.profiles?.find((item) => item.id === id); }

function renderProfile() {
  const profile = profileById($("profile").value);
  if (!profile) return;
  const sections = profile.sections || [];
  const required = sections.filter(section => section.required !== false).map(section => escapeHtml(section.title)).join("、");
  const optional = sections.filter(section => section.required === false).map(section => escapeHtml(section.title)).join("、");
  $("profileHint").innerHTML = `<strong>${escapeHtml(profile.title)}</strong><br>必填内容：${required || "无"}。${optional ? `<br>选填内容：${optional}。未提供的选填内容不会补成空段落。` : ""}`;
  if (!activeDocument) renderChecks(profile, null, `new:${profile.id}`);
}

function renderChecks(profile, evaluation = null, context = "") {
  const definitions = profile?.evaluation_checks || [];
  const renderKey = JSON.stringify([context, definitions, evaluation]);
  if (checksRenderKey === renderKey) return;
  checksRenderKey = renderKey;
  $("evaluationChecks").innerHTML = definitions.length ? definitions.map(item => `
    <div class="check-row">
      <label title="${escapeHtml(item.description)}"><input type="checkbox" data-check="${escapeHtml(item.id)}" ${evaluation?.checks?.[item.id] ? "checked" : ""}> <span><strong>${escapeHtml(item.title)}</strong><br><small>${escapeHtml(item.description)}</small></span></label>
      <input data-reason="${escapeHtml(item.id)}" value="${escapeHtml(evaluation?.failure_reasons?.[item.id] || "")}" placeholder="未通过原因（FAIL 时必填）" ${evaluation?.checks?.[item.id] ? "disabled" : ""}>
    </div>
  `).join("") : '<p class="muted">当前模板不可用，无法加载验收项。已有知识卡仍可查看。</p>';
  document.querySelectorAll("[data-check]").forEach(input => input.addEventListener("change", () => {
    const reason = document.querySelector(`[data-reason="${CSS.escape(input.dataset.check)}"]`);
    reason.disabled = input.checked;
    if (input.checked) reason.value = "";
  }));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function setFeedback(id, message, kind = "") {
  const node = $(id);
  node.textContent = message || "";
  node.className = `feedback ${kind}`;
}

function renderTimeline(row) {
  if ($("timeline").dataset.state === row.state) return;
  const current = STATE_INDEX[row.state] ?? 0;
  const failed = ["STOPPED", "INTERRUPTED", "EVALUATION_FAILED"].includes(row.state);
  $("timeline").innerHTML = STAGES.map(([key, label], index) => {
    const symbol = index < current ? "✓" : index + 1;
    return `<li data-index="${index}"><span class="dot">${symbol}</span>${label}</li>`;
  }).join("");
  $("timeline").dataset.state = row.state;
  [...$("timeline").children].forEach((node, index) => {
    if (index > current) return;
    setTimeout(() => {
      node.classList.add(index < current ? "done" : "current");
      if (failed && index === current) node.classList.add("fail");
    }, 70 + index * 95);
  });
}

function latestTask(documentId) {
  return (bootstrap?.tasks || []).find(task => task.document_id === documentId);
}

function appendTextElement(parent, tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function appendInlineText(parent, text) {
  // Only these two inline styles are interpreted. Model HTML remains literal text.
  const tokens = String(text).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g);
  tokens.forEach(token => {
    if (token.startsWith("**") && token.endsWith("**")) appendTextElement(parent, "strong", token.slice(2, -2));
    else if (token.startsWith("`") && token.endsWith("`")) appendTextElement(parent, "code", token.slice(1, -1));
    else parent.append(document.createTextNode(token));
  });
}

function renderSectionBody(parent, body) {
  const lines = String(body ?? "").replace(/<!--[\s\S]*?(?:-->|$)/g, "").trim().split(/\r?\n/);
  let paragraph = null;
  let list = null;
  let code = null;
  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      paragraph = null;
      list = null;
      if (code) code = null;
      else code = appendTextElement(appendTextElement(parent, "pre", ""), "code", "");
      continue;
    }
    if (code) { code.textContent += `${line}\n`; continue; }
    if (!line.trim()) { paragraph = null; list = null; continue; }
    const bullet = line.match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)$/);
    if (bullet) {
      paragraph = null;
      const kind = bullet[1] ? "UL" : "OL";
      if (!list || list.tagName !== kind) list = appendTextElement(parent, kind.toLowerCase(), "");
      appendInlineText(appendTextElement(list, "li", ""), bullet[2]);
      continue;
    }
    list = null;
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    if (heading) { appendInlineText(appendTextElement(parent, "h4", ""), heading[1]); paragraph = null; continue; }
    if (!paragraph) paragraph = appendTextElement(parent, "p", "");
    else paragraph.append(document.createTextNode("\n"));
    appendInlineText(paragraph, line);
  }
}

function renderKnowledgeCard(row) {
  const preview = $("documentPreview");
  const version = row.version;
  preview.hidden = !version;
  preview.replaceChildren();
  $("documentSource").hidden = !version;
  $("markdownSource").textContent = version?.markdown || "";
  if (!version) return;
  const card = version.document || {};
  const spec = card.provenance?.spec || row.spec || {};
  const profile = card.provenance?.profile || row.profile || profileById(row.profile_id);
  const metadata = spec.metadata || {};
  appendTextElement(preview, "div", "入库知识单元 · KNOWLEDGE CARD", "card-eyebrow");
  appendTextElement(preview, "h2", card.title || spec.title || row.document_id, "card-title");
  const labels = [metadata.domain, CARD_TYPES[metadata.card_type] || metadata.card_type];
  if (Array.isArray(metadata.tags)) labels.push(...metadata.tags);
  const chips = appendTextElement(preview, "div", "", "card-labels");
  labels.filter(value => typeof value === "string" && value.trim()).forEach(value => appendTextElement(chips, "span", value, "card-label"));
  chips.hidden = !chips.children.length;
  const sections = card.sections || {};
  const definitions = profile?.sections || [];
  const titles = Object.fromEntries(definitions.map(section => [section.id, section.title]));
  const ordered = [...new Set([...definitions.map(section => section.id), ...Object.keys(sections)])];
  ordered.forEach(id => {
    if (!String(sections[id] || "").trim()) return;
    const section = appendTextElement(preview, "section", "", "card-section");
    appendTextElement(section, "h3", titles[id] || id);
    renderSectionBody(section, sections[id]);
  });
  if (!Object.keys(sections).length) appendTextElement(preview, "p", "这份历史记录没有结构化正文，可展开下方 Markdown 源文查看。", "muted");
}

function renderDocument(row) {
  const changedDocument = activeDocument?.document_id !== row.document_id;
  activeDocument = row;
  activeId = row.document_id;
  if (changedDocument) {
    $("reviewForm").reset();
    $("publishForm").reset();
    $("evaluationForm").reset();
    $("documentSource").open = false;
    $("versionDiff").open = false;
    setFeedback("reviewFeedback", "");
    $("timeline").dataset.state = "";
  }
  $("emptyState").hidden = true;
  $("runView").hidden = false;
  $("activeTitle").textContent = row.spec?.title || row.document_id;
  $("activeDocumentId").textContent = row.document_id;
  $("stateBadge").textContent = row.state;
  renderTimeline(row);

  const task = latestTask(row.document_id);
  if (task?.status === "FAILED" || task?.status === "UNCERTAIN" || task?.status === "INTERRUPTED") {
    setFeedback("taskFeedback", `${task.status}：${task.error || "任务未完成"}`, "error");
  } else if (RUNNING.has(row.state)) {
    setFeedback("taskFeedback", "任务正在执行，页面会自动同步最新阶段。", "");
  } else if (row.state === "RETURNED") {
    setFeedback("taskFeedback", "人工审核已退回。修改输入后可使用同一知识卡 ID 重新生成新版本。", "error");
  } else {
    setFeedback("taskFeedback", `当前状态：${row.state}`, row.state.includes("PASSED") || row.state === "APPROVED" ? "success" : "");
  }

  const version = row.version;
  $("versionText").textContent = version ? `${version.version_id} · SHA-256 ${(version.content_sha256 || "").slice(0, 12)}` : "";
  $("versionText").title = version?.content_sha256 || "";
  const versionKey = `${row.document_id}:${version?.content_sha256 || "pending"}`;
  if (renderedVersionKey !== versionKey) {
    renderedVersionKey = versionKey;
    renderKnowledgeCard(row);
    renderVersionDiff(row.versions || []);
    if (!changedDocument) {
      $("reviewForm").reset();
      $("publishForm").reset();
      $("evaluationForm").reset();
      setFeedback("reviewFeedback", "");
    }
  }
  const audit = version?.audit;
  const auditBox = $("auditBox");
  if (!version) {
    auditBox.className = "audit-box empty";
    auditBox.textContent = "正在等待生成结果。";
  } else if (!audit) {
    auditBox.className = "audit-box empty";
    auditBox.textContent = "知识卡已生成，正在等待审计结果。";
  } else {
    auditBox.className = `audit-box ${audit.passed ? "pass" : "fail"}`;
    auditBox.textContent = audit.passed
      ? `✓ 机器审计通过 · ${audit.provider}${audit.model ? ` / ${audit.model}` : ""}`
      : `FAIL · ${(audit.findings || []).map(item => `${item.code}: ${item.message}`).join("；") || "审计未通过"}`;
  }

  $("reviewForm").hidden = row.state !== "PENDING_REVIEW";
  const profile = row.profile || profileById(row.profile_id);
  const profileAvailable = row.profile_available !== false && Boolean(profile);
  $("exportButton").disabled = !profileAvailable || row.state !== "APPROVED";
  $("publishButton").disabled = row.state !== "PENDING_PUBLICATION";
  $("evaluationButton").disabled = !profileAvailable || !["PUBLISHED", "EVALUATION_PASSED", "EVALUATION_FAILED"].includes(row.state);
  renderChecks(profileAvailable ? profile : null, row.latest_evaluation, versionKey);
  if (["PENDING_PUBLICATION", "PUBLISHED", "EVALUATION_PASSED", "EVALUATION_FAILED"].includes(row.state)) renderDownloads(row.document_id);
  else $("downloadLinks").innerHTML = "";
  if (!profileAvailable) setFeedback("releaseFeedback", `此知识卡使用的模板 ${row.profile_id || "（未知）"} 已不可用。可以查看历史正文；重新导出和验收需要恢复对应模板。`, "error");
  else if (row.state === "EVALUATION_PASSED") setFeedback("releaseFeedback", "PASS：本次发布后验收已保存，所有必需检查项通过。", "success");
  else if (row.state === "EVALUATION_FAILED") {
    const reasons = Object.entries(row.latest_evaluation?.failure_reasons || {}).map(([id, reason]) => `${id}：${reason}`).join("；");
    setFeedback("releaseFeedback", `FAIL：本次验收已保存。${reasons || "请根据未通过项修正后重新验收。"}`, "error");
  }
  else if (row.state === "PUBLISHED") setFeedback("releaseFeedback", "发布记录已保存，等待执行验收。", "");
  else if (row.state === "PENDING_PUBLICATION") setFeedback("releaseFeedback", "入库材料已导出：Markdown、JSON 和 ZIP 可在此下载。完成目标系统导入或发布后，请手动登记发布记录。", "success");
  else setFeedback("releaseFeedback", "");
}

function renderVersionDiff(versions) {
  const details = $("versionDiff");
  if (versions.length < 2) {
    details.hidden = true;
    $("diffPreview").textContent = "";
    return;
  }
  const before = versions[versions.length - 2].document || {};
  const after = versions[versions.length - 1].document || {};
  const sectionTitles = Object.fromEntries((after.provenance?.profile?.sections || []).map(section => [section.id, section.title]));
  const keys = [...new Set([...Object.keys(before.sections || {}), ...Object.keys(after.sections || {})])];
  const changed = keys.filter(key => before.sections?.[key] !== after.sections?.[key]);
  details.hidden = false;
  $("diffPreview").textContent = changed.length
    ? changed.map(key => `【${sectionTitles[key] || key}】\n- ${before.sections?.[key] || "（无）"}\n+ ${after.sections?.[key] || "（无）"}`).join("\n\n")
    : `正文段落相同；版本变化来自来源、模型追踪或其他版本元数据。\n- ${before.content_sha256}\n+ ${after.content_sha256}`;
}

function renderDownloads(documentId) {
  $("downloadLinks").innerHTML = ["md", "json", "zip"].map(kind => `<a href="/api/download?document=${encodeURIComponent(documentId)}&kind=${kind}">${kind.toUpperCase()}</a>`).join("");
}

function renderList() {
  const rows = bootstrap.documents || [];
  $("documentList").innerHTML = rows.length ? rows.map(row => `
    <button class="document-row" type="button" data-document="${escapeHtml(row.document_id)}">
      <strong>${escapeHtml(row.spec?.title || row.document_id)}</strong><span class="muted mono">${escapeHtml(row.document_id)} · ${escapeHtml(profileById(row.profile_id)?.title || row.profile_id)}</span><span class="state-badge">${escapeHtml(row.state)}</span>
    </button>`).join("") : `<div class="empty">工作区还没有知识卡。</div>`;
  document.querySelectorAll("[data-document]").forEach(button => button.addEventListener("click", () => selectDocument(button.dataset.document)));
}

async function loadBootstrap() {
  bootstrap = await api("/api/bootstrap");
  $("providerBadge").textContent = bootstrap.provider;
  $("workspaceText").textContent = `Workspace: ${bootstrap.workspace}`;
  $("sourceRootText").textContent = `Source root: ${bootstrap.source_root}`;
  $("allowedHostsText").textContent = bootstrap.allowed_hosts?.length
    ? `允许域名：${bootstrap.allowed_hosts.join(", ")}`
    : "当前未配置网页域名白名单；请使用本地来源或重启时添加 --allow-host";
  const selected = $("profile").value;
  $("profile").innerHTML = bootstrap.profiles.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`).join("");
  $("profile").value = selected && profileById(selected) ? selected : (profileById("knowledge_card") ? "knowledge_card" : bootstrap.profiles[0]?.id);
  renderProfile();
  renderList();
}

async function selectDocument(documentId, quiet = false) {
  clearTimeout(pollTimer);
  activeId = documentId;
  const request = ++selectionRequest;
  try {
    const row = await api(`/api/documents/${encodeURIComponent(documentId)}`);
    if (request !== selectionRequest || activeId !== documentId) return;
    renderDocument(row);
    if (!quiet) document.querySelectorAll(".panel")[1].scrollIntoView({behavior:"smooth", block:"start"});
    managePolling(row);
  } catch (error) {
    if (request === selectionRequest) {
      toast(error.message);
      if (activeDocument?.document_id === documentId) managePolling(activeDocument);
    }
  }
}

function managePolling(row) {
  clearTimeout(pollTimer);
  if (RUNNING.has(row.state)) pollTimer = setTimeout(async () => {
    if (activeId !== row.document_id) return;
    try {
      await loadBootstrap();
      if (activeId === row.document_id) await selectDocument(row.document_id, true);
    } catch (error) {
      if (activeId === row.document_id) { toast(error.message); managePolling(row); }
    }
  }, 900);
}

async function refreshDocument(documentId) {
  await loadBootstrap();
  if (activeId === documentId) await selectDocument(documentId, true);
}

function selectedDocument() {
  if (!activeDocument || activeDocument.document_id !== activeId) throw new Error("正在切换知识卡，请等待加载完成后再操作。");
  return activeDocument;
}

function actionError(feedbackId, documentId, error) {
  if (activeId === documentId) setFeedback(feedbackId, error.message, "error");
  else toast(`${documentId}：${error.message}`);
}

$("profile").addEventListener("change", renderProfile);
$("refreshButton").addEventListener("click", async () => {
  try { if (activeId) await refreshDocument(activeId); else await loadBootstrap(); }
  catch (error) { toast(error.message); }
});

$("runForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const metadata = JSON.parse($("metadata").value || "{}");
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("高级设置中的额外元数据必须是 JSON 对象。");
    metadata.domain = $("domain").value.trim();
    metadata.card_type = $("cardType").value;
    metadata.tags = [...new Set($("tags").value.split(/[,，]/).map(item => item.trim()).filter(Boolean))];
    const body = {
      spec: {document_id: $("documentId").value.trim(), title: $("title").value.trim(), profile_id: $("profile").value, audience: $("audience").value.trim(), objective: $("objective").value.trim(), metadata},
      source_paths: $("sources").value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
      source_urls: $("sourceUrls").value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
    };
    if (!body.source_paths.length && !body.source_urls.length) throw new Error("请至少填写一个本地或网页来源。");
    const result = await api("/api/run", {method:"POST", body:JSON.stringify(body)});
    activeId = result.document_id;
    toast("任务已提交，状态将自动更新。");
    await loadBootstrap();
    await selectDocument(activeId, true);
  } catch (error) { toast(error.message); }
});

$("reviewForm").addEventListener("submit", async event => {
  event.preventDefault();
  const documentId = activeId;
  const decision = document.querySelector('input[name="decision"]:checked').value;
  const reason = $("reviewReason").value.trim();
  if (decision === "return" && !reason) { toast("选择不通过时必须填写退回原因。"); return; }
  try {
    const row = selectedDocument();
    await api("/api/review", {method:"POST", body:JSON.stringify({document_id:row.document_id, document_sha256:row.version.content_sha256, reviewer:$("reviewer").value.trim(), decision, reason})});
    await refreshDocument(row.document_id);
    if (activeId === row.document_id) setFeedback("reviewFeedback", decision === "approve" ? "人工审核已通过并绑定当前版本。" : "已退回；原因已保存，可重新生成新版本。", decision === "approve" ? "success" : "error");
  } catch (error) { actionError("reviewFeedback", documentId, error); }
});

$("exportButton").addEventListener("click", async () => {
  const documentId = activeId;
  try {
    const row = selectedDocument();
    await api("/api/export", {method:"POST", body:JSON.stringify({document_id:row.document_id})});
    await refreshDocument(row.document_id);
  } catch (error) { actionError("releaseFeedback", documentId, error); }
});

$("publishForm").addEventListener("submit", async event => {
  event.preventDefault();
  const documentId = activeId;
  try {
    const row = selectedDocument();
    await api("/api/publish", {method:"POST", body:JSON.stringify({document_id:row.document_id, operator:$("operator").value.trim(), target:$("publishTarget").value.trim(), external_id:$("externalId").value.trim(), confirmed:$("publishConfirmed").checked})});
    await refreshDocument(row.document_id);
  } catch (error) { actionError("releaseFeedback", documentId, error); }
});

$("evaluationForm").addEventListener("submit", async event => {
  event.preventDefault();
  const documentId = activeId;
  const checks = Object.fromEntries([...document.querySelectorAll("[data-check]")].map(item => [item.dataset.check, item.checked]));
  const failure_reasons = Object.fromEntries([...document.querySelectorAll("[data-reason]")].filter(item => !checks[item.dataset.reason]).map(item => [item.dataset.reason, item.value.trim()]));
  try {
    const row = selectedDocument();
    await api("/api/evaluate", {method:"POST", body:JSON.stringify({document_id:row.document_id, evaluator:$("evaluator").value.trim(), checks, failure_reasons, notes:$("evaluationNotes").value.trim()})});
    await refreshDocument(row.document_id);
  } catch (error) { actionError("releaseFeedback", documentId, error); }
});

function retrievalBusy(busy) {
  ["rebuildIndex", "searchKnowledge", "retrievalSandbox", "retrievalQuestion", "retrievalAnswer"].forEach(id => $(id).disabled = busy);
}

$("retrievalSandbox").addEventListener("change", () => {
  $("retrievalResults").replaceChildren();
  $("retrievalFeedback").textContent = "已切换索引范围，请重新检索。";
});

$("rebuildIndex").addEventListener("click", async () => {
  retrievalBusy(true);
  $("retrievalFeedback").textContent = "正在生成向量并更新索引，请稍候…";
  $("retrievalResults").replaceChildren();
  try {
    const result = await api("/api/retrieval/index", {method:"POST", body:JSON.stringify({sandbox:$("retrievalSandbox").checked})});
    $("retrievalFeedback").textContent = `${result.sandbox ? "测试" : "正式"}索引已更新：${result.documents} 张卡，${result.chunks} 个片段；模型 ${result.model}。${result.documents ? "可以输入问题验证。" : "当前范围没有符合审核状态的卡片。"}`;
  } catch (error) { $("retrievalFeedback").textContent = `索引更新失败：${error.message}`; }
  finally { retrievalBusy(false); }
});

$("retrievalForm").addEventListener("submit", async event => {
  event.preventDefault();
  retrievalBusy(true);
  $("retrievalFeedback").textContent = "正在检索并核对回答依据…";
  $("retrievalResults").replaceChildren();
  try {
    const result = await api("/api/retrieval/search", {method:"POST", body:JSON.stringify({question:$("retrievalQuestion").value.trim(), sandbox:$("retrievalSandbox").checked, answer:$("retrievalAnswer").checked})});
    $("retrievalFeedback").textContent = `${result.sandbox ? "测试" : "正式"}索引命中 ${result.hits.length} 张卡。${result.hits.length ? "请核对下方证据与问题条件。" : "请先建立索引，或检查当前卡片审核状态。"}`;
    if (result.answer) {
      const answer = document.createElement("div");
      answer.className = "action-card";
      const heading = document.createElement("h3");
      heading.textContent = result.answer.answerable ? "依据知识库的回答" : "依据不足";
      const body = document.createElement("p");
      body.textContent = result.answer.answer;
      const refs = document.createElement("small");
      refs.textContent = `引用：${result.answer.citations.join("、") || "无"}`;
      answer.append(heading, body, refs);
      $("retrievalResults").append(answer);
    }
    result.hits.forEach(hit => {
      const detail = document.createElement("details");
      detail.className = "version-diff";
      const summary = document.createElement("summary");
      summary.textContent = `${hit.title} · 相似度 ${hit.score.toFixed(3)} · ${hit.chunk_id}`;
      const evidence = document.createElement("pre");
      const chunks = hit.evidence_chunks || [hit];
      evidence.textContent = chunks.map(c => `[${c.chunk_id}]\n${c.text}`).join("\n\n") + `\n\n版本：${hit.content_sha256}\n来源包：${hit.source_bundle_sha256}`;
      const open = document.createElement("button");
      open.type = "button"; open.className = "ghost"; open.textContent = "查看这张知识卡";
      open.addEventListener("click", () => { selectDocument(hit.document_id); $("documentPreview").scrollIntoView({behavior:"smooth", block:"start"}); });
      detail.append(summary, evidence, open);
      $("retrievalResults").append(detail);
    });
  } catch (error) { $("retrievalFeedback").textContent = `检索失败：${error.message}`; }
  finally { retrievalBusy(false); }
});

loadBootstrap().then(() => {
  if (bootstrap.documents?.length) selectDocument(bootstrap.documents[0].document_id, true);
}).catch(error => toast(error.message));

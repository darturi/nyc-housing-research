let csrfToken = null;
let lastPropertyQuery = null;
let propertyContinuation = null;
let lastAnswerJobId = null;
let activeAnswerJobId = null;
let activePropertyExportJobId = null;
let selectedCredentialSlot = "openai";
let selectedCredentialIsCustom = false;
let selectedAnswerPricingVerified = true;
let currentLocale = document.documentElement.lang || "en";
let matterCache = [];
const byId = (id) => document.getElementById(id);

async function applyLocale(locale, {persist = false} = {}) {
  const response = await fetch(`/api/v1/locales/${encodeURIComponent(locale)}`);
  if (!response.ok) throw new Error("Could not load the interface language.");
  const catalog = await response.json();
  currentLocale = catalog.locale;
  document.documentElement.lang = currentLocale;
  byId("ui-locale").value = currentLocale;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const translated = catalog.messages[element.dataset.i18n];
    if (translated) element.textContent = translated;
  });
  if (persist && csrfToken) {
    await api("/api/v1/settings", {
      method: "PATCH",
      body: JSON.stringify({ui_locale: currentLocale}),
    });
  }
}

byId("ui-locale").addEventListener("change", (event) => {
  applyLocale(event.target.value, {persist: true}).catch((error) => {
    byId("launch-status").textContent = error.message;
  });
});

function formatMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "$0.00";
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatDate(value) {
  if (!value) return "not yet checked";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "date unavailable";
  return new Intl.DateTimeFormat(undefined, {dateStyle: "medium"}).format(date);
}

function formatMonth(value) {
  const [year, month] = String(value).split("-").map(Number);
  if (!year || !month) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

function setCredentialOverview(present, provider, detail = null, checked = true) {
  const overview = byId("credential-overview");
  overview.classList.toggle("is-connected", checked && present);
  overview.classList.toggle("needs-attention", checked && !present);
  byId("credential-summary").textContent = checked
    ? (present ? "API key connected" : "No API key connected")
    : "API key not checked";
  byId("credential-summary-detail").textContent = detail || (checked
    ? (present
      ? `Ready for optional ${provider} features. Test the connection before relying on it.`
      : "Add a key to use optional answers and improved search.")
    : "Keyless search is ready. The app does not access your OS credential store during startup.");
  byId("credential-save").textContent = present ? "Replace key" : "Add key";
}

async function api(path, options = {}) {
  const request = {...options, headers: {...(options.headers || {})}};
  if (request.method && !["GET", "HEAD"].includes(request.method)) {
    request.headers["X-CSRF-Token"] = csrfToken;
    if (!(request.body instanceof FormData)) {
      request.headers["Content-Type"] = "application/json";
    }
  }
  const response = await fetch(path, request);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

async function connect(token) {
  const response = await fetch("/api/v1/session/exchange", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({launch_token: token}),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Could not connect this browser.");
  csrfToken = body.csrf_token;
  await showApplication();
}

async function reconnectExistingSession() {
  const response = await fetch("/api/v1/session/csrf");
  if (!response.ok) return false;
  csrfToken = (await response.json()).csrf_token;
  await showApplication();
  return true;
}

async function showApplication() {
  byId("connect-panel").hidden = true;
  byId("application").hidden = false;
  const [, , , , status] = await Promise.all([
    loadSources(),
    loadJobs(),
    loadSettings(),
    loadUsage(),
    loadWorkspaceStatus(),
    loadMatters(),
  ]);
  if (!status.legal_corpus.active_generation_id) selectView("sources");
}

function selectView(name) {
  document.querySelectorAll(".app-view").forEach((view) => { view.hidden = true; });
  document.querySelectorAll("[data-view]").forEach((item) => {
    const selected = item.dataset.view === name;
    item.setAttribute("aria-pressed", String(selected));
    item.classList.toggle("secondary-button", !selected);
  });
  byId(`view-${name}`).hidden = false;
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => selectView(button.dataset.view));
});

document.querySelectorAll("[data-guide-target]").forEach((button) => {
  button.addEventListener("click", () => selectView(button.dataset.guideTarget));
});

byId("setup-settings").addEventListener("click", () => selectView("settings"));

byId("research-source").addEventListener("change", (event) => {
  if (event.target.value.startsWith("user-")) {
    byId("research-scope").value = "user";
  } else if (event.target.value) {
    byId("research-scope").value = "core";
  }
});

byId("launch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await connect(byId("launch-code").value.trim());
    byId("launch-code").value = "";
  } catch (error) {
    byId("launch-status").textContent = error.message;
  }
});

byId("research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = byId("question").value.trim();
  const target = byId("research-result");
  target.className = "loading-state";
  target.textContent = "Retrieving public sources…";
  try {
    const selectedMode = byId("research-mode").value;
    const help = await api("/api/v1/help-resources/match", {
      method: "POST",
      body: JSON.stringify({question, language: currentLocale}),
    });
    renderHelpResources(help);
    if (["auto", "property"].includes(selectedMode)) {
      const route = await api("/api/v1/route", {
        method: "POST",
        body: JSON.stringify({question, mode: selectedMode}),
      });
      if (route.mode === "property") {
        if (!route.query) {
          target.className = "empty-state";
          target.textContent = route.message;
          return;
        }
        await runPropertySearch(route.query);
        target.className = "result-shell";
        target.textContent = "Property mode resolved the request below. Confirm the building identity before relying on records.";
        return;
      }
    }
    if (["auto", "search"].includes(selectedMode)) {
      const source = byId("research-source").value || undefined;
      const scope = byId("research-scope").value;
      const body = await api("/api/v1/search", {
        method: "POST",
        body: JSON.stringify({query: question, scope, ...(source ? {source} : {})}),
      });
      renderEvidence(target, body.results, `Generation ${body.generation_id}`);
    } else {
      const source = byId("research-source").value || undefined;
      const scope = byId("research-scope").value;
      const allowUnknownCost = !selectedAnswerPricingVerified;
      if (allowUnknownCost && !window.confirm(
        "This provider's price is unknown. Send this one-off answer request outside the app's USD budget caps? Provider charges may apply.",
      )) return;
      const created = await api("/api/v1/query", {
        method: "POST",
        body: JSON.stringify({
          question,
          scope,
          ...(source ? {source} : {}),
          ...(allowUnknownCost ? {allow_unknown_cost: true} : {}),
          answer_language: byId("answer-language").value,
          reading_style: byId("reading-style").value,
        }),
      });
      activeAnswerJobId = created.job_id;
      byId("answer-cancel").hidden = false;
      await streamAnswer(created.job_id, target);
    }
  } catch (error) {
    target.className = "error-state";
    target.textContent = error.message;
  }
});

async function streamAnswer(jobId, target) {
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/stream`);
    if (!response.ok) {
      const body = await response.json();
      throw new Error(body.error || `Request failed (${response.status}).`);
    }
    if (!response.body) {
      await pollAnswer(jobId, target);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    while (!terminal) {
      const {done, value} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        terminal = applyAnswerSnapshot(JSON.parse(line), target);
        if (terminal) break;
      }
      if (done) {
        if (buffer.trim() && !terminal) {
          terminal = applyAnswerSnapshot(JSON.parse(buffer), target);
        }
        if (!terminal) throw new Error("The answer stream ended before completion.");
      }
    }
  } finally {
    if (activeAnswerJobId === jobId) activeAnswerJobId = null;
    byId("answer-cancel").hidden = true;
  }
}

async function pollAnswer(jobId, target) {
  for (;;) {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    if (applyAnswerSnapshot(job, target)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

function applyAnswerSnapshot(job, target) {
  if (job.evidence.length) renderStreamingAnswer(target, job);
  if (job.state === "succeeded") {
    lastAnswerJobId = job.job_id;
    renderAnswer(target, job.result);
    return true;
  }
  if (["failed", "cancelled"].includes(job.state)) {
    throw new Error(job.error || "Answer job stopped.");
  }
  return false;
}

byId("answer-cancel").addEventListener("click", async () => {
  if (!activeAnswerJobId) return;
  try {
    await api(`/api/v1/jobs/${encodeURIComponent(activeAnswerJobId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    byId("research-result").textContent = "Cancellation requested…";
  } catch (error) {
    byId("research-result").textContent = error.message;
  }
});

function renderEvidence(target, rows, heading) {
  target.className = "result-shell";
  delete target.dataset.answerJob;
  target.replaceChildren();
  const header = document.createElement("div");
  header.className = "result-header evidence-header";
  const title = document.createElement("h3");
  const generationHeading = heading.startsWith("Generation ");
  title.textContent = generationHeading
    ? `${rows.length} matching passage${rows.length === 1 ? "" : "s"}`
    : heading;
  const meta = document.createElement("span");
  meta.className = "evidence-meta";
  meta.textContent = generationHeading
    ? heading
    : `${rows.length} cited passage${rows.length === 1 ? "" : "s"}`;
  header.append(title, meta);
  target.append(header);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.textContent = "No matching installed source text was found.";
    target.append(empty);
    return;
  }
  target.append(createCitationList(rows));
}

function createCitationList(rows) {
  const list = document.createElement("ol");
  list.className = "citation-list";
  rows.forEach((row, index) => {
    const item = document.createElement("li");
    item.className = "citation-item";
    const sourceNumber = String(row.marker || index + 1).replace(/^\D+/, "");
    item.dataset.sourceNumber = sourceNumber.padStart(2, "0");
    const label = document.createElement("strong");
    label.textContent = row.citation || row.title || row.source_name;
    const text = document.createElement("p");
    const evidenceText = row.excerpt || row.text || "";
    const collapsed = evidenceText.length > 900;
    text.textContent = collapsed ? `${evidenceText.slice(0, 900)}…` : evidenceText;
    const source = document.createElement("span");
    source.className = "citation-source";
    const effective = row.effective_from
      ? `effective from ${row.effective_from}${row.effective_to ? ` through ${row.effective_to}` : ""}`
      : "effective date not supplied";
    source.textContent = [
      row.source_name,
      row.publisher || "publisher not supplied",
      row.retrieved_at ? `retrieved ${row.retrieved_at}` : null,
      row.last_checked_at ? `checked ${row.last_checked_at}` : null,
      effective,
    ].filter(Boolean).join(" · ");
    item.append(label, text, source);
    if (collapsed) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "secondary-button";
      toggle.textContent = "Show full excerpt";
      toggle.addEventListener("click", () => {
        const expanded = toggle.textContent === "Show less";
        text.textContent = expanded ? `${evidenceText.slice(0, 900)}…` : evidenceText;
        toggle.textContent = expanded ? "Show full excerpt" : "Show less";
      });
      item.append(toggle);
    }
    if (row.source_url) {
      try {
        const url = new URL(row.source_url);
        if (url.protocol === "https:") {
          const link = document.createElement("a");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "Open official source";
          item.append(link);
        }
      } catch (_) {
        // Invalid publisher metadata is shown without creating a link.
      }
    }
    if (row.origin === "user" && row.source_version_id && row.chunk_id) {
      const resourceId = String(row.source_slug || "").replace(/^user-/, "");
      const link = document.createElement("a");
      link.href = `/api/v1/resources/${encodeURIComponent(resourceId)}/versions/${encodeURIComponent(row.source_version_id)}/chunks/${encodeURIComponent(row.chunk_id)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open saved excerpt";
      item.append(link);
    }
    list.append(item);
  });
  return list;
}

function appendAnswerHeader(target, titleText, metaText) {
  const header = document.createElement("div");
  header.className = "result-header evidence-header answer-header";
  const title = document.createElement("h3");
  title.textContent = titleText;
  const meta = document.createElement("span");
  meta.className = "evidence-meta";
  meta.textContent = metaText;
  header.append(title, meta);
  target.append(header);
}

function appendAnswerSources(target, rows) {
  if (!rows.length) return;
  const sources = document.createElement("details");
  sources.className = "answer-sources";
  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.textContent = "Sources used";
  const count = document.createElement("span");
  count.className = "evidence-meta";
  count.textContent = `${rows.length} passage${rows.length === 1 ? "" : "s"}`;
  summary.append(title, count);
  const guidance = document.createElement("p");
  guidance.className = "answer-sources-guidance";
  guidance.textContent = "Citation numbers in the answer open the relevant passage in place. This register contains the complete source record.";
  sources.append(summary, guidance, createCitationList(rows));
  target.append(sources);
}

function renderAnswer(target, result) {
  target.className = "result-shell answer-result";
  delete target.dataset.answerJob;
  target.replaceChildren();
  const status = result.status.replaceAll("_", " ");
  appendAnswerHeader(
    target,
    "Generated answer",
    `${status} · ${result.answer_profile_id} · ${result.evidence.length} cited passage${result.evidence.length === 1 ? "" : "s"}`,
  );
  const stage = createAnswerStage(target, result.evidence, false);
  renderMarkdown(stage.content, result.answer, result.evidence, stage.pane, stage.root);
  appendAnswerSources(target, result.evidence);
  const footer = document.createElement("div");
  footer.className = "answer-footer";
  const disclaimer = document.createElement("p");
  disclaimer.className = "meta-line";
  disclaimer.textContent = result.disclaimer;
  footer.append(disclaimer);
  if (!result.cost_known) {
    const costWarning = document.createElement("p");
    costWarning.className = "meta-line";
    costWarning.textContent = "Unknown provider cost; this request was outside USD budget caps.";
    footer.append(costWarning);
  }
  const actions = document.createElement("div");
  actions.className = "property-actions";
  [["Export Markdown", "markdown"], ["Export JSON", "json"]].forEach(([label, format]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = label;
    button.addEventListener("click", () => exportAnswer(format));
    actions.append(button);
  });
  if (lastAnswerJobId && matterCache.length) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Matter for saved answer");
    matterCache.forEach((matter) => {
      const option = document.createElement("option");
      option.value = matter.id;
      option.textContent = matter.title;
      select.append(option);
    });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "secondary-button";
    save.textContent = "Save answer to matter";
    save.addEventListener("click", async () => {
      try {
        await api(`/api/v1/matters/${encodeURIComponent(select.value)}/items`, {
          method: "POST",
          body: JSON.stringify({
            job_id: lastAnswerJobId,
            idempotency_key: crypto.randomUUID(),
          }),
        });
        save.textContent = "Saved to matter";
        save.disabled = true;
        await loadMatters();
      } catch (error) {
        save.textContent = error.message;
      }
    });
    actions.append(select, save);
  }
  if (result.status === "provider_error") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary-button";
    retry.textContent = "Retry answer";
    retry.addEventListener("click", () => {
      byId("research-mode").value = "answer";
      byId("research-form").requestSubmit();
    });
    actions.append(retry);
  }
  footer.append(actions);
  target.append(footer);
}

function renderStreamingAnswer(target, job) {
  let content = target.querySelector(".answer-markdown");
  let pane = target.querySelector(".citation-pane");
  let stage = target.querySelector(".answer-stage");
  if (target.dataset.answerJob !== job.job_id || !content || !pane || !stage) {
    target.className = "result-shell answer-result is-streaming";
    target.replaceChildren();
    appendAnswerHeader(
      target,
      "Generating answer…",
      `${job.evidence.length} passage${job.evidence.length === 1 ? "" : "s"} retrieved`,
    );
    target.dataset.answerJob = job.job_id;
    const created = createAnswerStage(target, job.evidence, true);
    content = created.content;
    pane = created.pane;
    stage = created.root;
    appendAnswerSources(target, job.evidence);
  }
  if (job.partial_answer) {
    content.className = "answer-text answer-markdown markdown-body";
    renderMarkdown(content, job.partial_answer, job.evidence, pane, stage);
  } else {
    content.className = "answer-text answer-markdown answer-pending";
    content.textContent = "Reviewing the retrieved passages and drafting a cited answer…";
  }
}

function createAnswerStage(target, evidence, streaming) {
  const root = document.createElement("div");
  root.className = "answer-stage";
  const article = document.createElement("article");
  article.className = "answer-panel";
  const label = document.createElement("div");
  label.className = "answer-label";
  const labelText = document.createElement("span");
  labelText.textContent = streaming ? "Answering" : "Answer";
  const guidance = document.createElement("span");
  guidance.className = "answer-citation-guidance";
  guidance.textContent = "Select a citation number to inspect its source in place";
  label.append(labelText, guidance);
  const content = document.createElement("div");
  content.className = "answer-text answer-markdown markdown-body";
  content.setAttribute("aria-live", "polite");
  content.setAttribute("aria-busy", String(streaming));
  if (streaming) {
    const indicator = document.createElement("span");
    indicator.className = "stream-indicator";
    indicator.textContent = "Streaming";
    label.append(indicator);
  }
  article.append(label, content);
  const pane = document.createElement("aside");
  pane.className = "citation-pane";
  pane.id = "answer-citation-pane";
  pane.setAttribute("aria-label", "Citation source");
  pane.hidden = true;
  root.append(article, pane);
  target.append(root);
  return {root, content, pane, evidence};
}

function renderMarkdown(target, markdown, evidence, pane, stage) {
  target.replaceChildren();
  const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^\s*```/.test(line)) {
      const language = line.trim().slice(3).trim();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      if (language) code.dataset.language = language;
      code.textContent = codeLines.join("\n");
      pre.append(code);
      target.append(pre);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${heading[1].length}`);
      appendInlineMarkdown(node, heading[2], evidence, pane, stage);
      target.append(node);
      index += 1;
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      target.append(document.createElement("hr"));
      index += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quoted = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoted.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      const blockquote = document.createElement("blockquote");
      renderMarkdown(blockquote, quoted.join("\n"), evidence, pane, stage);
      target.append(blockquote);
      continue;
    }
    const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = document.createElement(ordered ? "ol" : "ul");
      const pattern = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-+*]\s+(.+)$/;
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (!item) break;
        const listItem = document.createElement("li");
        appendInlineMarkdown(listItem, item[1], evidence, pane, stage);
        list.append(listItem);
        index += 1;
      }
      target.append(list);
      continue;
    }
    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, paragraphLines.join("\n"), evidence, pane, stage);
    target.append(paragraph);
  }
}

function isMarkdownBlockStart(line) {
  return /^\s*(?:```|#{1,6}\s|>|[-+*]\s+|\d+[.)]\s+|(?:-{3,}|\*{3,}|_{3,})\s*$)/.test(line);
}

function appendInlineMarkdown(target, text, evidence, pane, stage) {
  const evidenceByMarker = new Map(evidence.map((row) => [row.marker, row]));
  const token = /(\[(?:E|P)\d+\]|\[[^\]\n]+\]\(https:\/\/[^)\s]+\)|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|\*[^*\n]+\*|_[^_\n]+_|\n)/g;
  let cursor = 0;
  for (const match of text.matchAll(token)) {
    if (match.index > cursor) target.append(document.createTextNode(text.slice(cursor, match.index)));
    const value = match[0];
    const citation = value.match(/^\[((?:E|P)\d+)\]$/);
    const link = value.match(/^\[([^\]\n]+)\]\((https:\/\/[^)\s]+)\)$/);
    if (citation && evidenceByMarker.has(citation[1])) {
      const row = evidenceByMarker.get(citation[1]);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-chip";
      button.textContent = `[${citation[1].slice(1)}]`;
      button.setAttribute("aria-label", `View source ${citation[1].slice(1)}: ${row.citation || row.title || row.source_name}`);
      button.setAttribute("aria-controls", pane.id);
      button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", () => openCitationPane(pane, stage, row, citation[1], button));
      target.append(button);
    } else if (link) {
      const anchor = document.createElement("a");
      anchor.href = link[2];
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = link[1];
      target.append(anchor);
    } else if (value === "\n") {
      target.append(document.createElement("br"));
    } else if (value.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = value.slice(1, -1);
      target.append(code);
    } else {
      const tag = value.startsWith("**") || value.startsWith("__")
        ? "strong"
        : value.startsWith("~~") ? "s" : "em";
      const trim = tag === "strong" || tag === "s" ? 2 : 1;
      const node = document.createElement(tag);
      node.textContent = value.slice(trim, -trim);
      target.append(node);
    }
    cursor = match.index + value.length;
  }
  if (cursor < text.length) target.append(document.createTextNode(text.slice(cursor)));
}

function openCitationPane(pane, stage, row, marker, trigger) {
  const listItem = trigger.closest("li");
  const context = listItem || trigger.closest("p, blockquote, h1, h2, h3, h4, h5, h6, pre")
    || trigger.closest(".answer-markdown");
  stage.querySelectorAll(".is-citation-active").forEach((node) => {
    node.classList.remove("is-citation-active");
  });
  if (context) context.classList.add("is-citation-active");
  pane.replaceChildren();
  pane.hidden = false;
  stage.classList.add("has-open-citation");
  stage.querySelectorAll(".citation-chip").forEach((button) => {
    button.setAttribute("aria-expanded", String(button === trigger));
  });
  const header = document.createElement("div");
  header.className = "citation-pane-header";
  const eyebrow = document.createElement("span");
  eyebrow.textContent = `Source ${marker.slice(1)}`;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "citation-pane-close";
  close.setAttribute("aria-label", "Close source pane");
  close.textContent = "×";
  close.addEventListener("click", () => {
    pane.hidden = true;
    stage.classList.remove("has-open-citation");
    if (context) context.classList.remove("is-citation-active");
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
  });
  header.append(eyebrow, close);
  const title = document.createElement("h4");
  title.textContent = row.citation || row.title || row.source_name;
  const excerpt = document.createElement("p");
  excerpt.className = "citation-pane-excerpt";
  excerpt.textContent = row.excerpt || row.text || "No excerpt is available.";
  const metadata = document.createElement("p");
  metadata.className = "citation-source";
  metadata.textContent = [row.source_name, row.publisher, row.retrieved_at ? `retrieved ${row.retrieved_at}` : null].filter(Boolean).join(" · ");
  pane.append(header, title, excerpt, metadata);
  if (row.source_url) {
    try {
      const url = new URL(row.source_url);
      if (url.protocol === "https:") {
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open official source ↗";
        pane.append(link);
      }
    } catch (_) {
      // Invalid publisher metadata is shown without creating a link.
    }
  }
  if (context?.tagName === "LI") {
    context.append(pane);
  } else if (context) {
    context.insertAdjacentElement("afterend", pane);
  }
}

async function exportAnswer(format) {
  if (!lastAnswerJobId) return;
  const result = await api(`/api/v1/jobs/${encodeURIComponent(lastAnswerJobId)}/export`, {
    method: "POST",
    body: JSON.stringify({format}),
  });
  window.location.assign(result.download_url);
}

async function loadSources() {
  const body = await api("/api/v1/sources");
  const installedCount = body.sources.filter((source) => source.installed).length;
  byId("source-summary").textContent = installedCount
    ? `${installedCount} official ${installedCount === 1 ? "source is" : "sources are"} ready to search.`
    : "No official sources are installed yet.";
  byId("source-technical-summary").textContent = `${body.readiness}; ${body.chunk_count} indexed passages; generation ${body.active_generation_id || "none"}.`;
  const list = byId("source-list");
  const filter = byId("research-source");
  list.replaceChildren();
  filter.replaceChildren();
  const allSources = document.createElement("option");
  allSources.value = "";
  allSources.textContent = "All installed sources";
  filter.append(allSources);
  body.sources.forEach((source) => {
    if (source.installed) {
      const option = document.createElement("option");
      option.value = source.slug;
      option.textContent = source.name;
      filter.append(option);
    }
    if (source.role === "optional") return;
    const item = document.createElement("li");
    item.className = "citation-item";
    const title = document.createElement("strong");
    title.textContent = source.name;
    const scope = document.createElement("p");
    scope.textContent = source.scope;
    const status = document.createElement("span");
    status.className = "citation-source";
    status.textContent = source.installed
      ? `Ready · checked ${formatDate(source.last_checked_at)}`
      : "Not installed";
    item.append(title, scope, status);
    const update = document.createElement("button");
    update.type = "button";
    update.className = "secondary-button";
    update.textContent = source.installed ? "Update source" : "Install source";
    update.addEventListener("click", () => {
      const partial = body.is_partial || !body.active_generation_id;
      if (partial && !window.confirm(
        "This activates a partial corpus containing only currently installed and selected modules. Continue?",
      )) return;
      startCorpusJob("update", source.slug, partial);
    });
    item.append(update);
    list.append(item);
  });
  renderSourcePacks(body.source_packs || []);
  renderResources(body.resources || [], filter);
}

function renderSourcePacks(packs) {
  const list = byId("source-pack-list");
  list.replaceChildren();
  packs.forEach((pack) => {
    const item = document.createElement("li");
    item.className = "citation-item";
    const title = document.createElement("strong");
    title.textContent = pack.title;
    const detail = document.createElement("p");
    detail.textContent = pack.description;
    const status = document.createElement("span");
    status.className = "citation-source";
    status.textContent = `${pack.status.replaceAll("_", " ")} · ${pack.installed_module_count}/${pack.module_count} modules · ${pack.support_status} coverage`;
    const coverage = document.createElement("p");
    coverage.className = "meta-line";
    coverage.textContent = pack.missing_modules.length
      ? `Still missing: ${pack.missing_modules.join("; ")}.`
      : `Topics: ${pack.supported_topics.join("; ")}.`;
    const actions = document.createElement("div");
    actions.className = "property-actions";
    const install = resourceButton(
      pack.installed_module_count ? "Update pack" : "Install pack",
      () => startPackJob(pack.id, pack.installed_module_count ? "update" : "install"),
    );
    const check = resourceButton("Check for updates", () => startPackJob(pack.id, "check"));
    actions.append(install, check);
    if (pack.installed_module_count) {
      actions.append(resourceButton("Remove pack", () => removePack(pack)));
    }
    if (pack.restorable_module_count) {
      actions.append(resourceButton("Restore pack", () => startPackJob(pack.id, "restore")));
    }
    item.append(title, detail, status, coverage, actions);
    list.append(item);
  });
}

async function startPackJob(packId, operation, apply = false) {
  const target = byId("source-pack-action-status");
  target.textContent = `${operation.replaceAll("_", " ")} started…`;
  try {
    const job = await api(`/api/v1/source-packs/${encodeURIComponent(packId)}/jobs`, {
      method: "POST",
      body: JSON.stringify({operation, ...(apply ? {apply: true} : {})}),
    });
    await loadJobs();
    await pollCorpusJob(job.id, target);
    target.textContent = `${operation.replaceAll("_", " ")} complete.`;
  } catch (error) {
    target.textContent = error.message;
  }
}

async function removePack(pack) {
  const target = byId("source-pack-action-status");
  try {
    const preview = await api(`/api/v1/source-packs/${encodeURIComponent(pack.id)}/jobs`, {
      method: "POST",
      body: JSON.stringify({operation: "remove"}),
    });
    if (!window.confirm(
      `Remove ${preview.installed_modules_to_remove.length} active module(s)? ${preview.saved_evidence_impact}`,
    )) return;
    await startPackJob(pack.id, "remove", true);
  } catch (error) {
    target.textContent = error.message;
  }
}

function renderResources(resources, filter) {
  const list = byId("resource-list");
  list.replaceChildren();
  if (!resources.length) {
    const empty = document.createElement("li");
    empty.textContent = "No personal resources have been added.";
    list.append(empty);
    return;
  }
  resources.forEach((resource) => {
    const item = document.createElement("li");
    item.className = "citation-item resource-item";
    const title = document.createElement("strong");
    title.textContent = resource.name;
    const detail = document.createElement("p");
    detail.textContent = [
      resource.category,
      resource.publisher,
      resource.jurisdiction,
      `${resource.chunk_count} passage${resource.chunk_count === 1 ? "" : "s"}`,
    ].filter(Boolean).join(" · ");
    const status = document.createElement("span");
    status.className = "citation-source";
    status.textContent = `${resource.active ? "Searchable" : "Removed"} · ${resource.model_use_allowed ? "provider use allowed" : "local search only"} · added ${formatDate(resource.added_at)}`;
    item.append(title, detail, status);

    const actions = document.createElement("div");
    actions.className = "property-actions resource-actions";
    if (resource.active) {
      const option = document.createElement("option");
      option.value = resource.slug;
      option.textContent = `My resource: ${resource.name}`;
      filter.append(option);
      actions.append(resourceButton("Replace file", () => replaceResource(resource)));
      actions.append(resourceButton("Edit details", () => editResource(resource)));
      if (resource.media_type === "application/pdf") {
        actions.append(resourceButton("Inspect text / OCR need", () => inspectResource(resource)));
      }
      actions.append(resourceButton(
        resource.model_use_allowed ? "Disable provider use" : "Allow provider use",
        () => setResourceModelUse(resource, !resource.model_use_allowed),
      ));
      actions.append(resourceButton("Remove", () => mutateResource(resource.id, "remove", {})));
    } else {
      actions.append(resourceButton("Restore", () => mutateResource(resource.id, "restore", {})));
    }
    const versionId = resource.active_version_id || resource.latest_version_id;
    if (versionId) {
      const download = document.createElement("a");
      download.href = `/api/v1/resources/${encodeURIComponent(resource.id)}/versions/${encodeURIComponent(versionId)}/download`;
      download.textContent = "Download original";
      actions.append(download);
    }
    item.append(actions);
    list.append(item);
  });
}

function resourceButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary-button";
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function mutateResource(resourceId, operation, payload) {
  const target = byId("resource-action-status");
  try {
    const result = await api(`/api/v1/resources/${encodeURIComponent(resourceId)}/${operation}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    target.textContent = `Resource ${result.result.status.replaceAll("_", " ")}.`;
    await Promise.all([loadSources(), loadJobs()]);
  } catch (error) {
    target.textContent = error.message;
  }
}

async function setResourceModelUse(resource, allowed) {
  if (allowed && !window.confirm(
    "Allow excerpts from this resource to be embedded or sent to the configured model provider when you explicitly use provider-backed features?",
  )) return;
  await mutateResource(resource.id, "model-use", {allowed});
}

async function inspectResource(resource) {
  const target = byId("resource-action-status");
  try {
    const result = await api(`/api/v1/resources/${encodeURIComponent(resource.id)}/extraction-jobs`, {
      method: "POST",
      body: JSON.stringify({operation: "inspect"}),
    });
    target.textContent = `${result.report.embedded_text_pages}/${result.report.page_count} pages have useful embedded text; ${result.report.ocr_candidate_pages} page(s) are OCR candidates. ${result.report.ocr_runtime_reason}`;
  } catch (error) {
    target.textContent = error.message;
  }
}

async function editResource(resource) {
  const title = window.prompt("Resource title", resource.name);
  if (title === null) return;
  const publisher = window.prompt("Publisher or author", resource.publisher || "");
  if (publisher === null) return;
  const category = window.prompt("Category", resource.category || "reference");
  if (category === null) return;
  const jurisdiction = window.prompt("Jurisdiction", resource.jurisdiction || "");
  if (jurisdiction === null) return;
  const originalUrl = window.prompt("Original HTTPS URL (optional)", resource.original_url || "");
  if (originalUrl === null) return;
  const target = byId("resource-action-status");
  try {
    const result = await api(`/api/v1/resources/${encodeURIComponent(resource.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        title,
        publisher,
        category,
        jurisdiction,
        original_url: originalUrl,
        expected_version_id: resource.active_version_id,
      }),
    });
    target.textContent = `Resource ${result.result.status.replaceAll("_", " ")}.`;
    await Promise.all([loadSources(), loadJobs()]);
  } catch (error) {
    target.textContent = error.message;
  }
}

function replaceResource(resource) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".pdf,.docx,.md,.markdown,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown";
  input.addEventListener("change", async () => {
    if (!input.files?.length) return;
    const form = new FormData();
    form.append("file", input.files[0]);
    form.append("expected_version_id", resource.active_version_id);
    await submitResourceUpload(
      `/api/v1/resources/${encodeURIComponent(resource.id)}/versions`, form,
    );
  });
  input.click();
}

async function submitResourceUpload(path, form) {
  const target = byId("resource-action-status");
  try {
    target.textContent = "Importing and validating the resource…";
    const job = await api(path, {method: "POST", body: form});
    await loadJobs();
    await pollResourceJob(job.id);
  } catch (error) {
    target.textContent = error.message;
  }
}

async function pollResourceJob(jobId) {
  const target = byId("resource-action-status");
  for (;;) {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    target.textContent = `${job.stage.replaceAll("_", " ")} · ${job.state.replaceAll("_", " ")}`;
    if (["succeeded", "failed", "cancelled"].includes(job.state)) {
      await Promise.all([loadSources(), loadJobs(), loadWorkspaceStatus()]);
      if (job.state === "failed") throw new Error(job.error_message || "Resource import failed.");
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

byId("resource-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await submitResourceUpload("/api/v1/resources", form);
  if (!byId("resource-action-status").textContent.toLowerCase().includes("failed")) {
    event.currentTarget.reset();
    byId("resource-category").value = "reference";
  }
});

async function loadWorkspaceStatus() {
  const body = await api("/api/v1/status");
  byId("setup-guidance").hidden = Boolean(
    body.legal_corpus.active_generation_id,
  );
  byId("setup-data-path").textContent = body.data_dir;
  const target = byId("workspace-summary");
  target.replaceChildren();
  const rows = [
    ["Application", body.application_version],
    ["Data", body.data_dir],
    ["Configuration", body.config_file],
    ["Corpus", body.legal_corpus.readiness],
    ["Corpus schema", body.schema_versions.corpus],
    ["State schema", body.schema_versions.state],
    ["Property cache", `${body.property_cache.entry_count} entries · ${body.property_cache.size_bytes} bytes`],
    ["Network policy", body.offline ? "offline" : "network available for explicit actions"],
  ];
  rows.forEach(([term, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    target.append(dt, dd);
  });
  byId("diagnostics-status").textContent = body.maintenance.active
    ? `Maintenance active: ${body.maintenance.operation}.`
    : "Workspace stores are available.";
  return body;
}

async function loadJobs() {
  const body = await api("/api/v1/jobs?limit=20");
  const list = byId("job-list");
  list.replaceChildren();
  if (!body.jobs.length) {
    const item = document.createElement("li");
    item.textContent = "No local jobs have run yet.";
    list.append(item);
    return;
  }
  const jobNames = {
    corpus_install: "Source installation",
    corpus_update: "Source update",
    corpus_check: "Source update check",
    corpus_remove: "Source-pack removal",
    corpus_restore: "Source-pack restoration",
    corpus_verify: "Source check",
    corpus_rollback: "Source rollback",
    corpus_index: "Search quality update",
    property_complete_export: "Property export",
    resource_add: "Resource import",
    resource_replace: "Resource replacement",
    resource_edit: "Resource details update",
    resource_remove: "Resource removal",
    resource_restore: "Resource restoration",
    resource_model_use: "Resource provider permission",
  };
  const stateNames = {
    queued: "Waiting",
    running: "In progress",
    paused: "Paused",
    cancel_requested: "Cancelling",
    cancelled: "Cancelled",
    failed: "Needs attention",
    succeeded: "Complete",
  };
  body.jobs.forEach((job) => {
    const item = document.createElement("li");
    item.className = "citation-item";
    const title = document.createElement("strong");
    title.textContent = `${jobNames[job.job_type] || job.job_type.replaceAll("_", " ")} · ${stateNames[job.state] || job.state}`;
    const detail = document.createElement("p");
    const progress = job.progress_total === null
      ? String(job.progress_current)
      : `${job.progress_current}/${job.progress_total}`;
    detail.textContent = `${job.stage.replaceAll("_", " ")}; progress ${progress}; updated ${formatDate(job.updated_at)}.`;
    item.append(title, detail);
    if (job.error_message) {
      const error = document.createElement("p");
      error.className = "error-state";
      error.textContent = job.error_message;
      item.append(error);
    }
    if (["queued", "running", "paused", "cancel_requested"].includes(job.state)) {
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary-button";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => controlJob(job.id, "cancel"));
      item.append(cancel);
    }
    const resumable = job.job_type.startsWith("corpus_")
      || ["resource_add", "resource_replace"].includes(job.job_type)
      || job.job_type === "property_complete_export";
    if (["paused", "failed"].includes(job.state) && job.retryable && resumable) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "secondary-button";
      resume.textContent = "Resume";
      resume.addEventListener("click", () => controlJob(job.id, "resume"));
      item.append(resume);
    }
    list.append(item);
  });
}

async function controlJob(jobId, action) {
  const target = byId("source-action-status");
  try {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/${action}`, {
      method: "POST",
      body: "{}",
    });
    target.textContent = `${action} requested for ${job.job_id || job.id}.`;
    await loadJobs();
    if (action === "resume") {
      if (job.job_type === "property_complete_export") {
        await pollPropertyExportJob(job.job_id || job.id, target);
      } else {
        await pollCorpusJob(job.job_id || job.id);
      }
    }
  } catch (error) {
    target.textContent = error.message;
  }
}

async function startCorpusJob(operation, source = null, allowPartial = false) {
  const target = byId("source-action-status");
  target.textContent = `${operation === "install" ? "Installing" : "Updating"} official sources…`;
  try {
    const job = await api("/api/v1/corpus/jobs", {
      method: "POST",
      body: JSON.stringify({
        operation,
        ...(source ? {source} : {}),
        ...(allowPartial ? {allow_partial: true} : {}),
      }),
    });
    await loadJobs();
    await pollCorpusJob(job.id);
  } catch (error) {
    target.textContent = error.message;
  }
}

let lastIndexEstimate = null;

async function estimateSemanticIndex() {
  const target = byId("index-action-status");
  target.textContent = "Estimating the remaining work and maximum provider cost…";
  try {
    const estimate = await api("/api/v1/corpus/index-estimate");
    lastIndexEstimate = estimate;
    const cost = `$${estimate.estimated_cost_usd}`;
    const credential = estimate.credential_present
      ? "Your API key is ready."
      : "Add an API key in Settings to continue.";
    target.textContent = `${estimate.chunks_requiring_embedding} of ${estimate.total_chunks} passages need processing; ${estimate.reusable_chunks} can be reused. Estimated maximum cost ${cost}. ${credential}`;
    byId("index-build").disabled = !estimate.credential_present;
    return estimate;
  } catch (error) {
    lastIndexEstimate = null;
    byId("index-build").disabled = true;
    target.textContent = error.message;
    throw error;
  }
}

async function buildSemanticIndex() {
  const target = byId("index-action-status");
  try {
    const estimate = lastIndexEstimate || await estimateSemanticIndex();
    const ceiling = estimate.estimated_cost_usd;
    if (estimate.paid && !window.confirm(
      `Improve search quality with a maximum provider charge of $${ceiling}?`,
    )) return;
    target.textContent = "Starting semantic-index job…";
    const job = await api("/api/v1/corpus/jobs", {
      method: "POST",
      body: JSON.stringify({
        operation: "index",
        approve_cost: estimate.paid,
        max_cost_usd: ceiling,
      }),
    });
    lastIndexEstimate = null;
    byId("index-build").disabled = true;
    await loadJobs();
    await pollCorpusJob(job.id);
    target.textContent = `Semantic index job ${job.id} completed.`;
  } catch (error) {
    target.textContent = error.message;
  }
}

async function pollCorpusJob(jobId, target = byId("source-action-status")) {
  for (;;) {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    const progress = job.progress_total === null
      ? String(job.progress_current)
      : `${job.progress_current}/${job.progress_total}`;
    target.textContent = `${job.stage}; progress ${progress}; ${job.state}.`;
    await loadJobs();
    if (["succeeded", "failed", "cancelled"].includes(job.state)) {
      await Promise.all([loadSources(), loadWorkspaceStatus()]);
      if (job.state === "failed") throw new Error(job.error_message || "Corpus job failed.");
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function renderHelpResources(body) {
  const target = byId("help-resources");
  target.replaceChildren();
  if (body.message) {
    const message = document.createElement("p");
    message.className = "meta-line";
    message.textContent = body.message;
    target.append(message);
  }
  (body.cards || []).forEach((card) => {
    const article = document.createElement("article");
    article.className = "citation-item";
    const title = document.createElement("h3");
    title.textContent = card.title;
    const text = document.createElement("p");
    text.textContent = card.body;
    const review = document.createElement("p");
    review.className = "meta-line";
    review.textContent = `NYC resource · catalog ${body.catalog_version} · ${body.editorial_status.replaceAll("_", " ")}`;
    const actions = document.createElement("div");
    actions.className = "property-actions";
    card.actions.forEach((action) => {
      const link = document.createElement("a");
      link.textContent = action.label;
      link.rel = "noopener noreferrer";
      if (action.type === "telephone") {
        link.href = `tel:${action.destination}`;
      } else {
        link.href = action.destination;
        link.target = "_blank";
      }
      actions.append(link);
    });
    article.append(title, text, review, actions);
    target.append(article);
  });
}

byId("help-resources-open").addEventListener("click", async () => {
  try {
    renderHelpResources(await api(
      `/api/v1/help-resources?language=${encodeURIComponent(currentLocale)}`,
    ));
  } catch (error) {
    byId("help-resources").textContent = error.message;
  }
});

async function loadMatters() {
  const body = await api("/api/v1/matters?include_archived=false");
  matterCache = body.matters;
  const target = byId("matter-list");
  target.replaceChildren();
  if (!matterCache.length) {
    const item = document.createElement("li");
    item.textContent = "No saved matters yet.";
    target.append(item);
    return;
  }
  matterCache.forEach((matter) => {
    const item = document.createElement("li");
    item.className = "citation-item";
    const title = document.createElement("strong");
    title.textContent = matter.title;
    const description = document.createElement("p");
    description.textContent = matter.description || "No description.";
    const meta = document.createElement("span");
    meta.className = "citation-source";
    meta.textContent = `${matter.item_count} saved item${matter.item_count === 1 ? "" : "s"} · revision ${matter.revision} · ${matter.tags.join(", ") || "no tags"}`;
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.className = "secondary-button";
    exportButton.textContent = "Export matter";
    exportButton.addEventListener("click", async () => {
      try {
        const result = await api(`/api/v1/matters/${encodeURIComponent(matter.id)}/export`, {
          method: "POST",
          body: "{}",
        });
        window.location.assign(result.download_url);
      } catch (error) {
        byId("matter-status").textContent = error.message;
      }
    });
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "secondary-button";
    openButton.textContent = "Open matter";
    openButton.addEventListener("click", () => {
      openMatter(matter.id).catch((error) => {
        byId("matter-status").textContent = error.message;
      });
    });
    const archiveButton = document.createElement("button");
    archiveButton.type = "button";
    archiveButton.className = "secondary-button";
    archiveButton.textContent = "Archive";
    archiveButton.addEventListener("click", async () => {
      try {
        await api(`/api/v1/matters/${encodeURIComponent(matter.id)}`, {
          method: "PATCH",
          body: JSON.stringify({expected_revision: matter.revision, archived: true}),
        });
        byId("matter-detail").hidden = true;
        await loadMatters();
      } catch (error) {
        byId("matter-status").textContent = error.message;
      }
    });
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "secondary-button";
    deleteButton.textContent = "Delete matter";
    deleteButton.addEventListener("click", async () => {
      try {
        const preview = await api(`/api/v1/matters/${encodeURIComponent(matter.id)}`, {
          method: "DELETE",
        });
        if (!window.confirm(
          `Delete this matter? ${preview.linked_item_count} saved item link(s) will be removed; shared saved payloads are retained.`,
        )) return;
        await api(`/api/v1/matters/${encodeURIComponent(matter.id)}?apply=true`, {
          method: "DELETE",
        });
        byId("matter-detail").hidden = true;
        await loadMatters();
      } catch (error) {
        byId("matter-status").textContent = error.message;
      }
    });
    const actions = document.createElement("div");
    actions.className = "property-actions";
    actions.append(openButton, exportButton, archiveButton, deleteButton);
    item.append(title, description, meta, actions);
    target.append(item);
  });
}

async function openMatter(matterId) {
  const matter = await api(`/api/v1/matters/${encodeURIComponent(matterId)}`);
  const target = byId("matter-detail");
  target.replaceChildren();
  target.hidden = false;

  const heading = document.createElement("div");
  heading.className = "panel-heading";
  const headingCopy = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = matter.title;
  const description = document.createElement("p");
  description.textContent = matter.description || "No description.";
  headingCopy.append(title, description);
  heading.append(headingCopy);
  target.append(heading);

  const itemHeading = document.createElement("h3");
  itemHeading.textContent = "Saved items";
  const items = document.createElement("ul");
  items.className = "citation-list";
  if (!matter.items.length) {
    const empty = document.createElement("li");
    empty.textContent = "No results have been saved to this matter.";
    items.append(empty);
  }
  matter.items.forEach((savedItem) => {
    const row = document.createElement("li");
    row.className = "citation-item";
    const label = document.createElement("strong");
    label.textContent = savedItem.kind.replaceAll("_", " ");
    const meta = document.createElement("span");
    meta.className = "citation-source";
    meta.textContent = `Saved ${formatDate(savedItem.created_at)} · SHA-256 ${savedItem.payload_hash}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary-button";
    remove.textContent = "Remove from matter";
    remove.addEventListener("click", async () => {
      try {
        const base = `/api/v1/saved-items/${encodeURIComponent(savedItem.id)}?matter_id=${encodeURIComponent(matter.id)}`;
        const preview = await api(base, {method: "DELETE"});
        const consequence = preview.saved_item_will_be_deleted
          ? "This is its final matter link, so the saved payload will also be deleted."
          : "The immutable payload remains linked to another matter.";
        if (!window.confirm(`Remove this item? ${consequence}`)) return;
        await api(`${base}&apply=true`, {method: "DELETE"});
        await Promise.all([openMatter(matter.id), loadMatters()]);
      } catch (error) {
        byId("matter-status").textContent = error.message;
      }
    });
    row.append(label, meta, remove);
    items.append(row);
  });
  target.append(itemHeading, items);

  const noteHeading = document.createElement("h3");
  noteHeading.textContent = "Notes";
  const notes = document.createElement("div");
  notes.className = "settings-stack";
  matter.notes.forEach((note) => {
    const form = document.createElement("form");
    form.className = "query-form";
    const label = document.createElement("label");
    label.textContent = `Note · revision ${note.revision}`;
    const input = document.createElement("textarea");
    input.value = note.body;
    input.maxLength = 50000;
    label.append(input);
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "Save note";
    form.append(label, save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api(`/api/v1/notes/${encodeURIComponent(note.id)}`, {
          method: "PATCH",
          body: JSON.stringify({
            body: input.value,
            expected_revision: note.revision,
            matter_id: matter.id,
          }),
        });
        await openMatter(matter.id);
      } catch (error) {
        byId("matter-status").textContent = error.message;
      }
    });
    notes.append(form);
  });
  const newNote = document.createElement("form");
  newNote.className = "query-form";
  const newNoteLabel = document.createElement("label");
  newNoteLabel.textContent = "New note";
  const newNoteBody = document.createElement("textarea");
  newNoteBody.maxLength = 50000;
  newNoteLabel.append(newNoteBody);
  const addNote = document.createElement("button");
  addNote.type = "submit";
  addNote.textContent = "Add note";
  newNote.append(newNoteLabel, addNote);
  newNote.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api(`/api/v1/matters/${encodeURIComponent(matter.id)}/notes`, {
        method: "POST",
        body: JSON.stringify({body: newNoteBody.value}),
      });
      await openMatter(matter.id);
    } catch (error) {
      byId("matter-status").textContent = error.message;
    }
  });
  notes.append(newNote);
  target.append(noteHeading, notes);
}

byId("matter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/matters", {
      method: "POST",
      body: JSON.stringify({
        title: byId("matter-title").value,
        description: byId("matter-description").value,
        tags: byId("matter-tags").value.split(",").map((item) => item.trim()).filter(Boolean),
      }),
    });
    event.target.reset();
    byId("matter-status").textContent = "Matter created.";
    await loadMatters();
  } catch (error) {
    byId("matter-status").textContent = error.message;
  }
});

byId("matters-refresh").addEventListener("click", () => {
  loadMatters().catch((error) => { byId("matter-status").textContent = error.message; });
});

byId("source-install").addEventListener("click", () => startCorpusJob("install"));
byId("source-update").addEventListener("click", () => startCorpusJob("update"));
byId("index-estimate").addEventListener("click", estimateSemanticIndex);
byId("index-build").addEventListener("click", buildSemanticIndex);

byId("source-verify").addEventListener("click", async () => {
  try {
    const result = await api("/api/v1/corpus/verify", {method: "POST", body: "{}"});
    byId("source-action-status").textContent = `Verified ${result.chunk_count} chunks in generation ${result.generation_id}.`;
  } catch (error) {
    byId("source-action-status").textContent = error.message;
  }
});

byId("source-rollback").addEventListener("click", async () => {
  try {
    const result = await api("/api/v1/corpus/rollback", {method: "POST", body: "{}"});
    byId("source-action-status").textContent = `Active generation is now ${result.generation_id}.`;
    await loadSources();
  } catch (error) {
    byId("source-action-status").textContent = error.message;
  }
});

async function loadSettings() {
  const [settings, profiles] = await Promise.all([
    api("/api/v1/settings"),
    api("/api/v1/profiles"),
  ]);
  byId("monthly-budget").value = settings.monthly_budget_usd;
  byId("operation-budget").value = settings.per_operation_budget_usd;
  byId("paid-concurrency").value = String(settings.max_concurrent_paid_requests);
  byId("answer-deadline").value = String(settings.answer_deadline_seconds);
  byId("property-cache-max").value = settings.property_cache_max_mb;
  byId("property-cache-retention").value = settings.property_cache_retention_days;
  byId("operational-retention").value = settings.operational_retention_days;
  byId("usage-retention").value = settings.usage_retention_months;
  byId("offline-mode").checked = settings.offline;
  byId("default-answer-language").value = settings.answer_language;
  byId("default-reading-style").value = settings.reading_style;
  byId("answer-language").value = settings.answer_language;
  byId("reading-style").value = settings.reading_style;
  if (settings.ui_locale !== currentLocale) await applyLocale(settings.ui_locale);
  const selectedProfiles = [
    profiles.profiles.find((item) => item.id === settings.answer_profile),
    profiles.profiles.find((item) => item.id === settings.embedding_profile),
  ];
  selectedAnswerPricingVerified = selectedProfiles[0]?.pricing_verified !== false;
  const credentialProfile = selectedProfiles.find(
    (item) => item?.provider === "openai-compatible",
  ) || selectedProfiles.find((item) => item && item.provider !== "fake");
  selectedCredentialSlot = credentialProfile?.credential_slot || "openai";
  selectedCredentialIsCustom = credentialProfile?.provider === "openai-compatible";
  byId("provider-key-label").textContent = selectedCredentialSlot === "openai"
    ? "OpenAI API key"
    : `${selectedCredentialSlot} API key`;
  setCredentialOverview(false, selectedCredentialSlot, null, false);
  byId("credential-status").textContent = "";
  byId("credential-validate").disabled = selectedCredentialIsCustom;
  byId("credential-validate").textContent = selectedCredentialIsCustom
    ? "Test custom connection in the terminal"
    : "Test connection (may charge)";
}

async function loadCredentialStatus() {
  const target = byId("credential-status");
  target.textContent = "Checking the OS credential store; macOS may request permission.";
  try {
    const result = await api(
      `/api/v1/credentials/${encodeURIComponent(selectedCredentialSlot)}`,
    );
    const credential = result.credential;
    const present = Boolean(credential?.present);
    setCredentialOverview(present, selectedCredentialSlot);
    target.textContent = present
      ? "A saved key exists. It is write-only and has not been tested in this session."
      : "No saved key was found.";
    if (credential?.source === "keyring") {
      byId("credential-storage").value = "keyring";
    } else if (credential?.source === "secret_file") {
      byId("credential-storage").value = "file";
    }
  } catch (error) {
    target.textContent = error.message;
  }
}

byId("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/settings", {
      method: "PATCH",
      body: JSON.stringify({
        monthly_budget_usd: byId("monthly-budget").value,
        per_operation_budget_usd: byId("operation-budget").value,
        max_concurrent_paid_requests: Number.parseInt(byId("paid-concurrency").value, 10),
        answer_deadline_seconds: Number.parseInt(byId("answer-deadline").value, 10),
        property_cache_max_mb: Number.parseInt(byId("property-cache-max").value, 10),
        property_cache_retention_days: Number.parseInt(byId("property-cache-retention").value, 10),
        operational_retention_days: Number.parseInt(byId("operational-retention").value, 10),
        usage_retention_months: Number.parseInt(byId("usage-retention").value, 10),
        offline: byId("offline-mode").checked,
        answer_language: byId("default-answer-language").value,
        reading_style: byId("default-reading-style").value,
      }),
    });
    byId("settings-status").textContent = "Changes saved. New work and provider requests use the updated settings.";
    await loadUsage();
  } catch (error) {
    byId("settings-status").textContent = error.message;
  }
});

byId("offline-readiness-check").addEventListener("click", async () => {
  const target = byId("offline-readiness-result");
  target.textContent = "Checking installed local capabilities…";
  try {
    const result = await api("/api/v1/offline/readiness");
    const summary = Object.entries(result.capabilities).map(([name, value]) => (
      `${name.replaceAll("_", " ")}: ${value.state}${value.reason_code ? ` (${value.reason_code})` : ""}`
    ));
    target.textContent = summary.join(" · ");
  } catch (error) {
    target.textContent = error.message;
  }
});

byId("diagnostics-refresh").addEventListener("click", async () => {
  try {
    await loadWorkspaceStatus();
  } catch (error) {
    byId("diagnostics-status").textContent = error.message;
  }
});

byId("property-cache-clear").addEventListener("click", async () => {
  try {
    const result = await api("/api/v1/properties/cache", {
      method: "DELETE",
      body: "{}",
    });
    byId("cache-clear-status").textContent = `Removed ${result.removed_entries} cached artifacts.`;
  } catch (error) {
    byId("cache-clear-status").textContent = error.message;
  }
});

async function runRetention(apply) {
  const target = byId("cache-clear-status");
  try {
    const result = await api("/api/v1/maintenance/prune", {
      method: "POST",
      body: JSON.stringify({apply}),
    });
    const action = result.applied ? "Removed" : "Would remove";
    target.textContent = `${action} ${result.candidate_jobs} old jobs, ${result.candidate_usage_events} usage events, ${result.candidate_cache_entries} cache entries, and ${result.candidate_log_files} log files. Protected ${result.protected_unresolved_usage_attempts} unresolved usage attempts and ${result.protected_pinned_cache_entries} pinned cache entries.`;
    if (result.applied) await Promise.all([loadJobs(), loadUsage()]);
  } catch (error) {
    target.textContent = error.message;
  }
}

byId("retention-preview").addEventListener("click", () => runRetention(false));
byId("retention-apply").addEventListener("click", () => runRetention(true));

byId("credential-check").addEventListener("click", loadCredentialStatus);

byId("credential-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("provider-key");
  try {
    const result = await api(`/api/v1/credentials/${encodeURIComponent(selectedCredentialSlot)}`, {
      method: "PUT",
      body: JSON.stringify({
        credential: input.value,
        storage: byId("credential-storage").value,
      }),
    });
    input.value = "";
    await loadSettings();
    setCredentialOverview(true, selectedCredentialSlot);
    byId("credential-status").textContent = result.profiles_activated
      ? "Key saved securely. The managed OpenAI models are now configured; restart before model-backed work."
      : "Key saved securely on this device. Test the connection before relying on it.";
  } catch (error) {
    input.value = "";
    byId("credential-status").textContent = error.message;
  }
});

byId("credential-validate").addEventListener("click", async () => {
  const target = byId("credential-status");
  if (selectedCredentialIsCustom) {
    target.textContent = "Test this custom connection with the profiles check in the terminal.";
    return;
  }
  try {
    const estimate = await api(
      "/api/v1/credentials/openai/validation-estimate",
    );
    if (!window.confirm(
      `Validate the stored OpenAI credential using ${estimate.model}? Approve a hard ceiling of $${estimate.estimated_cost_usd}.`,
    )) return;
    target.textContent = "Testing the connection…";
    const result = await api("/api/v1/credentials/openai/validate", {
      method: "POST",
      body: JSON.stringify({
        approve_cost: true,
        max_cost_usd: estimate.estimated_cost_usd,
      }),
    });
    setCredentialOverview(true, selectedCredentialSlot, `Connection tested successfully with ${result.model}.`);
    target.textContent = `Connection successful. This test recorded ${formatMoney(result.cost_usd)} in usage.`;
  } catch (error) {
    target.textContent = error.message;
  }
});

byId("property-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = {
    building_id: byId("building-id").value.trim() || null,
    registration_id: byId("registration-id").value.trim() || null,
    house_number: byId("house-number").value.trim() || null,
    street_name: byId("street-name").value.trim() || null,
    borough: byId("borough").value || null,
    zip_code: byId("zip-code").value.trim() || null,
    violation_class: byId("violation-class").value || null,
    status: byId("violation-status").value || null,
    inspection_date_from: byId("inspection-date-from").value || null,
    inspection_date_to: byId("inspection-date-to").value || null,
    limit: 50,
  };
  Object.keys(query).forEach((key) => query[key] === null && delete query[key]);
  await runPropertySearch(query);
});

async function runPropertySearch(query) {
  const target = byId("property-result");
  target.textContent = "Checking the official HPD source or compatible local cache…";
  byId("property-next").hidden = true;
  byId("property-refresh").hidden = true;
  byId("property-summary").hidden = true;
  byId("property-dossier").hidden = true;
  byId("property-export").hidden = true;
  byId("property-export-complete").hidden = true;
  byId("property-export-cancel").hidden = true;
  try {
    const body = await api("/api/v1/properties/search", {
      method: "POST",
      body: JSON.stringify(query),
    });
    const {refresh: _refresh, ...baseQuery} = query;
    lastPropertyQuery = baseQuery;
    propertyContinuation = body.next_cursor || body.continuation;
    renderProperty(target, body);
  } catch (error) {
    target.className = "error-state";
    target.textContent = error.message;
  }
}

function renderProperty(target, body) {
  target.className = "hpd-table-wrap";
  target.replaceChildren();
  byId("property-dossier").hidden = body.requires_selection || !body.records.length;
  const status = document.createElement("p");
  status.className = "meta-line";
  const total = body.total_count === null ? "total not requested" : `${body.total_count} total`;
  const cacheStatus = body.cache_status.replaceAll("_", " ");
  status.textContent = `${cacheStatus}; fetched ${body.fetch_started_at || body.fetched_at} to ${body.fetch_completed_at || body.fetched_at}; ${body.returned_count} rows (${total}); ${body.is_complete ? "complete loaded scope" : "more or unresolved"}. ${body.notice}`;
  target.append(status);
  try {
    const officialUrl = new URL(body.dataset_url);
    if (officialUrl.protocol === "https:") {
      const official = document.createElement("a");
      official.href = officialUrl.href;
      official.target = "_blank";
      official.rel = "noopener noreferrer";
      official.textContent = "Open official HPD dataset";
      target.append(official);
    }
  } catch (_) {
    // Invalid packaged publisher metadata is shown without a link.
  }
  if (body.requires_selection) {
    const prompt = document.createElement("p");
    prompt.textContent = "Select the intended HPD building:";
    const list = document.createElement("div");
    list.className = "candidate-list";
    body.candidates.forEach((candidate) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary-button";
      button.textContent = `${candidate.house_number} ${candidate.street_name}, ${candidate.borough} · building ${candidate.building_id}`;
      button.addEventListener("click", () => runPropertySearch({
        ...lastPropertyQuery,
        building_id: candidate.building_id,
        registration_id: null,
        house_number: null,
        street_name: null,
        borough: null,
        zip_code: null,
        continuation: null,
      }));
      list.append(button);
    });
    target.append(prompt, list);
    return;
  }
  const table = document.createElement("table");
  table.className = "hpd-table";
  const caption = document.createElement("caption");
  caption.textContent = "Loaded HPD Housing Maintenance Code violation records";
  table.append(caption);
  const head = document.createElement("thead");
  const headingRow = document.createElement("tr");
  ["Violation", "Class", "Inspection", "Status", "Description"].forEach((label) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    headingRow.append(th);
  });
  head.append(headingRow);
  const bodyElement = document.createElement("tbody");
  body.records.forEach((record) => {
    const row = document.createElement("tr");
    [record.violation_id, record.violation_class, record.inspection_date, record.current_status || record.violation_status, record.description].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value || "—";
      row.append(cell);
    });
    bodyElement.append(row);
  });
  table.append(head, bodyElement);
  target.append(table);
  byId("property-refresh").hidden = false;
  byId("property-next").hidden = !body.has_more;
  byId("property-summary").hidden = !body.records.length;
  byId("property-export").hidden = !body.records.length;
  byId("property-export-complete").hidden = !body.records.length || body.requires_selection;
}

byId("property-refresh").addEventListener("click", async () => {
  if (lastPropertyQuery) {
    await runPropertySearch({...lastPropertyQuery, refresh: true});
  }
});

byId("property-next").addEventListener("click", async () => {
  if (lastPropertyQuery && propertyContinuation) {
    await runPropertySearch({...lastPropertyQuery, continuation: propertyContinuation});
  }
});

byId("property-summary").addEventListener("click", async () => {
  if (!lastPropertyQuery) return;
  const target = byId("property-result");
  try {
    const allowUnknownCost = !selectedAnswerPricingVerified;
    if (allowUnknownCost && !window.confirm(
      "This provider's price is unknown. Send this one-off summary request outside the app's USD budget caps? Provider charges may apply.",
    )) return;
    const result = await api("/api/v1/properties/summarize", {
      method: "POST",
      body: JSON.stringify({
        ...lastPropertyQuery,
        ...(allowUnknownCost ? {allow_unknown_cost: true} : {}),
      }),
    });
    const panel = document.createElement("div");
    panel.className = "result-shell";
    const title = document.createElement("h3");
    title.textContent = `Summary · ${result.status}`;
    const copy = document.createElement("p");
    copy.textContent = result.summary;
    const scope = document.createElement("p");
    scope.className = "meta-line";
    const cost = result.cost_known
      ? `cost $${result.cost_usd}`
      : "unknown cost outside USD budget caps";
    scope.textContent = `Fixed rows: ${result.property_evidence_ids.join(", ")}; complete: ${result.property_is_complete}; fetched: ${result.property_fetched_at}; ${cost}.`;
    panel.append(title, copy, scope);
    target.prepend(panel);
  } catch (error) {
    target.textContent = error.message;
  }
});

byId("property-export").addEventListener("click", async () => {
  if (!lastPropertyQuery) return;
  try {
    const result = await api("/api/v1/properties/export", {
      method: "POST",
      body: JSON.stringify(lastPropertyQuery),
    });
    window.location.assign(result.download_url);
  } catch (error) {
    byId("property-result").textContent = error.message;
  }
});

byId("property-export-complete").addEventListener("click", async () => {
  if (!lastPropertyQuery) return;
  const target = byId("property-result");
  try {
    const created = await api("/api/v1/exports", {
      method: "POST",
      body: JSON.stringify({
        kind: "property_complete",
        query: {...lastPropertyQuery, continuation: null},
        max_pages: 20,
        deadline_seconds: 120,
      }),
    });
    activePropertyExportJobId = created.id;
    byId("property-export-cancel").hidden = false;
    await pollPropertyExportJob(created.id, target);
  } catch (error) {
    target.textContent = error.message;
  } finally {
    activePropertyExportJobId = null;
    byId("property-export-cancel").hidden = true;
  }
});

byId("property-dossier").addEventListener("click", async () => {
  if (!lastPropertyQuery) return;
  const target = byId("property-result");
  try {
    const identity = await api("/api/v1/properties/resolve", {
      method: "POST",
      body: JSON.stringify(lastPropertyQuery),
    });
    if (identity.requires_selection) {
      target.textContent = "Confirm one of the returned buildings before creating a dossier.";
      return;
    }
    const dossier = await api(`/api/v1/properties/${encodeURIComponent(identity.id)}/dossiers`, {
      method: "POST",
      body: JSON.stringify({query: lastPropertyQuery, panels: ["hpd_violations"]}),
    });
    const notice = document.createElement("p");
    notice.className = "meta-line";
    notice.textContent = `Dossier ${dossier.id} saved locally with payload hash ${dossier.payload_hash}.`;
    target.prepend(notice);
  } catch (error) {
    target.textContent = error.message;
  }
});

async function pollPropertyExportJob(jobId, target) {
  for (;;) {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    const pages = job.resume.page_count || job.progress_current;
    target.textContent = `Full export: ${job.state}; ${pages}/${job.progress_total || 20} pages.`;
    await loadJobs();
    if (["succeeded", "failed", "cancelled"].includes(job.state)) {
      if (job.resume.output_filename) {
        window.location.assign(`/api/v1/exports/${encodeURIComponent(job.resume.output_filename)}`);
      } else if (job.state === "failed") {
        throw new Error(job.error_message || "Complete property export failed.");
      }
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

byId("property-export-cancel").addEventListener("click", async () => {
  if (!activePropertyExportJobId) return;
  try {
    await api(`/api/v1/jobs/${encodeURIComponent(activePropertyExportJobId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    byId("property-result").textContent = "Cancelling complete property export…";
  } catch (error) {
    byId("property-result").textContent = error.message;
  }
});

async function loadUsage() {
  const body = await api("/api/v1/usage");
  const spent = Number(body.settled_usd) || 0;
  const cap = Number(body.cap_usd) || 0;
  const remaining = Number(body.remaining_usd) || 0;
  const usedPercent = cap > 0
    ? Math.min(100, Math.max(0, ((cap - remaining) / cap) * 100))
    : 0;
  byId("usage-primary").textContent = `${formatMoney(spent)} spent of ${formatMoney(cap)}`;
  byId("usage-secondary").textContent = `${formatMoney(remaining)} remaining · ${formatMonth(body.month)}`;
  byId("usage-progress").value = usedPercent;
  byId("usage-progress").textContent = `${Math.round(usedPercent)}%`;
  const target = byId("usage-summary");
  target.replaceChildren();
  const rows = [
    ["Month", body.month],
    ["Confirmed spend", formatMoney(body.settled_usd)],
    ["Reserved for work in progress", formatMoney(body.reserved_usd)],
    ["Pending confirmation", formatMoney(body.uncertain_usd)],
    ["Remaining", formatMoney(body.remaining_usd)],
    ["Simultaneous paid requests", body.max_concurrent_paid_requests],
    ["Requests without known pricing", body.unknown_cost_attempts],
    ["Unresolved requests without known pricing", body.unknown_cost_in_flight + body.unknown_cost_uncertain],
  ];
  if (body.history_pruned_before) {
    rows.push(["History retained since", body.history_pruned_before]);
  }
  rows.forEach(([term, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    target.append(dt, dd);
  });
}

byId("demo-search").addEventListener("click", async () => {
  const target = byId("demo-result");
  try {
    const response = await fetch("/api/v1/demo/search", {method: "POST"});
    const body = await response.json();
    target.textContent = `${body.warning} ${body.results[0].excerpt}`;
  } catch (error) {
    target.textContent = error.message;
  }
});

const fragment = new URLSearchParams(window.location.hash.slice(1));
const fragmentToken = fragment.get("launch");
applyLocale(currentLocale).catch(() => false);
if (fragmentToken) {
  history.replaceState(null, "", window.location.pathname + window.location.search);
  connect(fragmentToken).catch((error) => {
    byId("launch-status").textContent = error.message;
  });
} else {
  reconnectExistingSession().catch(() => false);
}

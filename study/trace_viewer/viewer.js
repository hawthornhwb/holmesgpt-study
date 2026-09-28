"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const offline = JSON.parse($("trace-data").textContent);
  const state = {trace: null, selected: 0, tab: "flow", query: "", run: "", loading: 0};
  const statusText = {success: "成功", error: "失败", approval_required: "需要审批", pending: "未收到结果", finished: "进程已结束", failed: "异常结束", incomplete: "未记录结束", unreadable: "无法读取"};
  const text = value => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "（未提供）";
  const short = (value, length = 100) => text(value).replace(/\s+/g, " ").slice(0, length);
  const el = (tag, className = "", content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  };
  const badge = (value, label) => el("span", `badge ${["error", "failed", "approval_required"].includes(value) ? "bad" : ["success", "finished"].includes(value) ? "good" : "wait"}`, label || statusText[value] || value);
  const pre = value => el("pre", "", value === null || value === undefined ? "（未提供）" : text(value));
  const inline = value => {
    const fragment = document.createDocumentFragment();
    for (const part of value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
      fragment.append(part.startsWith("**") && part.endsWith("**") ? el("strong", "", part.slice(2, -2)) : part.startsWith("`") && part.endsWith("`") ? el("code", "", part.slice(1, -1)) : document.createTextNode(part));
    }
    return fragment;
  };
  // Small text-only Markdown renderer: trace content can never inject HTML or remote resources.
  function prose(value) {
    const root = el("div", "prose");
    let fence = null, code = [], paragraph = [], list = null;
    const flush = () => { if (paragraph.length) { const p = el("p"); p.append(inline(paragraph.join("\n"))); root.append(p); paragraph = []; } list = null; };
    for (const line of text(value).split("\n")) {
      const marker = line.match(/^\s*(`{3,}|~{3,})/);
      if (fence) {
        if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length) { root.append(pre(code.join("\n"))); code = []; fence = null; }
        else code.push(line);
      } else if (marker) { flush(); fence = marker[1]; }
      else if (!line.trim()) flush();
      else if (/^#{1,6}\s/.test(line)) { flush(); const heading = el("h3"); heading.append(inline(line.replace(/^#+\s/, ""))); root.append(heading); }
      else if (/^\s*(?:[-*]|\d+\.)\s/.test(line)) {
        if (paragraph.length) flush();
        const tag = /^\s*\d+\./.test(line) ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); root.append(list); }
        const item = el("li"); item.append(inline(line.replace(/^\s*(?:[-*]|\d+\.)\s/, ""))); list.append(item);
      } else { list = null; paragraph.push(line); }
    }
    flush(); if (fence) root.append(pre(code.join("\n")));
    return root;
  }
  function disclosure(title, render, key = title) {
    const details = el("details"); details.dataset.key = key;
    details.append(el("summary", "", title));
    details.addEventListener("toggle", () => {
      if (details.open && details.children.length === 1) {
        const body = el("div", "details-body"); body.append(render()); details.append(body);
      }
    });
    return details;
  }
  function card(title, content, meta = "", className = "") {
    const root = el("article", `card ${className}`), header = el("div", "card-header");
    const name = el("div", "card-title"); name.append(el("span", "card-icon", "◇"), el("span", "", title));
    header.append(name, el("div", "card-meta", meta));
    const body = el("div", "card-body"); body.append(content); root.append(header, body); return root;
  }
  const eventData = event => event?.data || {};
  const argsFor = row => { const data = eventData(row.request); return data.kwargs || data; };
  const titleFor = row => row.loop === null || row.loop === undefined ? `辅助请求 ${row.request_id}` : `Loop ${String(row.loop).padStart(2, "0")}`;
  const source = event => el("div", "source-line", event ? `事件 #${event.seq ?? "?"} · +${event.elapsed_s ?? "?"}s · ${event.source || "未记录源码位置"}` : "");
  function notice(message) { $("notice").hidden = !message; $("notice").textContent = message; }
  function overview() {
    const summary = state.trace.summary, root = $("overview"); root.replaceChildren();
    const top = el("div", "overview-top"); top.append(el("span", "eyebrow", "一次问题，一条完整的执行路径"), badge(summary.status));
    const model = el("div", "model-line"); model.append(el("span", "model-name", summary.model), el("span", "run-name", summary.name));
    if (summary.auxiliary_count) model.append(el("span", "", `另有 ${summary.auxiliary_count} 次辅助请求`));
    const stats = el("div", "stats");
    for (const [value, unit, label] of [[summary.round_count, "轮", "模型调用"], [summary.tool_count, "次", "工具请求"], [summary.tool_errors, "次", "工具失败 / 待审批"], [Number(summary.duration_s || 0).toFixed(1), "s", "记录时长"]]) {
      const item = el("div", "stat"), amount = el("div", "stat-value", String(value)); amount.append(el("span", "stat-unit", unit)); item.append(amount, el("div", "stat-label", label)); stats.append(item);
    }
    root.append(top, el("h1", "question-long", summary.question), model, stats);
    $("run-date").textContent = summary.started ? new Date(summary.started).toLocaleString("zh-CN", {hour12:false}) : "未记录启动时间";
    $("round-count").textContent = `${state.trace.rounds.length} 次请求`;
  }
  function navigation() {
    const nav = $("round-nav"); nav.replaceChildren(); let visible = 0;
    state.trace.rounds.forEach((row, index) => {
      if (state.query && !JSON.stringify(row).toLowerCase().includes(state.query.toLowerCase())) return;
      visible++;
      const response = eventData(row.response), button = el("button", `round-link ${index === state.selected ? "active" : ""}`);
      button.setAttribute("aria-current", index === state.selected ? "step" : "false");
      const body = el("span"), name = el("span", "round-label", titleFor(row));
      if (row.error || row.tools.some(tool => ["error", "approval_required"].includes(tool.status))) name.append(el("span", "error-dot", " ·"));
      body.append(name, el("span", "round-description", short(response.content || (row.error ? "模型请求失败" : row.response ? "未返回公开说明" : "等待模型响应"), 140)), el("span", "round-mini", `${row.tools.length} 个工具${response.duration_s !== undefined ? ` · ${response.duration_s}s` : ""}`));
      button.append(el("span", "round-index", row.loop ?? "·"), body); button.addEventListener("click", () => select(index)); nav.append(button);
    });
    if (!visible) nav.append(el("div", "search-empty", state.query ? "没有匹配的轮次" : "尚无模型请求"));
  }
  function toolCard(tool, index) {
    const call = tool.call, func = call.function || {}, raw = eventData(tool.raw).result, final = eventData(tool.final).result;
    const result = final || raw, denied = raw?.status === "approval_required";
    const details = disclosure("", () => {
      const root = el("div"); let parameters = func.arguments ?? eventData(tool.start).params;
      try { parameters = JSON.parse(parameters); } catch (_) { /* Preserve non-JSON arguments verbatim. */ }
      root.append(el("div", "section-label", "调用参数"), pre(parameters));
      if (denied) root.append(el("p", "pending-note", "工具要求审批。是否实际执行，请以本次结果和错误说明为准。"));
      const error = eventData(tool.error).error || result?.error;
      if (error) { const block = pre(error); block.classList.add("error-output"); root.append(el("div", "section-label", "错误 / 拒绝原因"), block); }
      root.append(el("div", "section-label", final ? "循环收到的工具结果" : "工具原始返回"));
      root.append(result ? pre(result.data ?? "（没有输出数据）") : el("p", "pending-note", tool.error ? "工具异常，未返回结果。" : "日志中尚未收到结果，可能仍在执行或已中断。"));
      if (raw && final && JSON.stringify(raw) !== JSON.stringify(final)) root.append(disclosure("对照：裁剪 / 审批处理前的原始返回", () => pre(raw), `raw-tool-${index}`));
      if (result?.return_code !== null && result?.return_code !== undefined) root.append(el("div", "tool-id", `返回码 ${result.return_code}`));
      root.append(el("div", "tool-id", `调用 ID：${call.id || "未记录"}`), source(tool.final || tool.raw || tool.error || tool.start)); return root;
    }, `tool-${index}`);
    details.className = "tool-call-details";
    const summary = details.firstElementChild; summary.replaceChildren();
    const head = el("div", "tool-summary"), label = el("div", "tool-heading"); label.append(el("span", "muted", String(index + 1).padStart(2, "0")), el("span", "tool-name", func.name || call.name || "未知工具"));
    head.append(label, badge(tool.status)); summary.append(head);
    let parameters = func.arguments;
    try { parameters = JSON.parse(parameters); } catch (_) { /* Display as recorded. */ }
    const preview = typeof parameters === "object" && parameters !== null ? parameters.command || parameters.cmd || (Array.isArray(parameters.todos) ? `${parameters.todos.length} 项任务 · ${parameters.todos.map(todo => todo.content).join("；")}` : text(parameters)) : parameters || "（无参数）";
    summary.append(el("div", "tool-command", short(preview, 240)));
    const root = el("article", `card tool-card ${["error", "approval_required"].includes(tool.status) ? "has-error" : ""}`); root.append(details); return root;
  }
  function flow(row) {
    const root = document.createDocumentFragment(), response = eventData(row.response), args = argsFor(row);
    const meta = el("div", "metric-row");
    meta.append(el("span", "badge", `${(args.messages || []).length} 条输入消息`));
    if (response.duration_s !== undefined) meta.append(el("span", "badge", `模型耗时 ${response.duration_s}s`));
    if (response.usage?.total_tokens !== undefined) meta.append(el("span", "badge", `${response.usage.total_tokens.toLocaleString()} tokens`));
    root.append(meta);
    if (row.error) root.append(card("模型请求失败", pre(eventData(row.error)), "", "error-card"));
    if (!row.response) { root.append(el("div", "empty", row.error ? "本轮没有模型响应。可在输入上下文中检查请求。" : "日志尚未记录模型响应。可刷新查看，或检查运行概览中的异常。")); return root; }
    const body = el("div"); body.append(response.content ? prose(response.content) : el("p", "muted", "本轮未返回公开说明；请查看工具请求。"));
    body.append(disclosure(response.reasoning_content ? "模型返回的 reasoning · 展开查看" : "reasoning · 本轮未提供", () => response.reasoning_content ? prose(response.reasoning_content) : el("p", "muted", "API 没有返回 reasoning_content，不推测或补写。"), "reasoning"), source(row.response));
    root.append(card(row.tools.length ? "模型回复" : "模型回复 · 无工具请求", body, `请求 ${row.request_id}`, !row.tools.length ? "answer-card" : ""));
    if (row.tools.length) { root.append(el("div", "flow-connector", `模型请求了 ${row.tools.length} 个工具，点击查看参数与执行结果`)); row.tools.forEach((tool, i) => root.append(toolCard(tool, i))); }
    root.append(el("div", "transition", `${response.next || (row.tools.length ? "携带工具结果进入下一轮" : "没有工具请求")} · finish_reason: ${response.finish_reason || "未记录"}`));
    return root;
  }
  function context(row) {
    const root = document.createDocumentFragment(), args = argsFor(row), messages = args.messages || [];
    root.append(el("p", "context-hint", `本轮请求快照包含 ${messages.length} 条消息。展开可查看此前的模型回复与工具结果如何进入本轮上下文。`));
    messages.forEach((message, index) => {
      const details = disclosure(`${String(index + 1).padStart(2, "0")}  ·  ${message.role || "unknown"}${message.name ? ` / ${message.name}` : ""}`, () => {
        const body = el("div"); body.append(pre(message.content));
        if (message.tool_calls) body.append(el("div", "section-label", "工具请求"), pre(message.tool_calls));
        if (message.reasoning_content) body.append(disclosure("reasoning_content", () => pre(message.reasoning_content)));
        body.append(disclosure("完整消息对象", () => pre(message))); return body;
      }, `message-${index}`);
      details.firstElementChild.append(el("span", "message-preview", short(message.content || message.tool_calls || "（无文本）", 100))); root.append(details);
    });
    const {messages: omitted, ...settings} = args;
    root.append(disclosure("工具 Schema 与其他请求参数", () => pre(settings)), source(row.request)); return root;
  }
  function rawEvents(events) {
    const root = document.createDocumentFragment();
    events.forEach((event, i) => { const details = disclosure(`#${event.seq ?? "?"}  ${event.event}  ·  +${event.elapsed_s ?? "?"}s`, () => pre(event), `event-${i}`); details.classList.add("raw-event"); root.append(details); });
    return root;
  }
  function runOverview() {
    const root = document.createDocumentFragment();
    if (state.trace.errors.length) { root.append(card("运行中的异常", rawEvents(state.trace.errors), "", "error-card")); }
    if (state.trace.answer) root.append(card("最终记录的回答", prose(state.trace.answer), "", "answer-card"));
    else root.append(el("div", "empty", "日志尚未记录最终回答。"));
    root.append(disclosure("运行统计与退出状态", () => pre(state.trace.summary)), el("div", "section-label", "启动、结束与其他事件"), rawEvents(state.trace.other_events)); return root;
  }
  function renderDetail(preserve = false) {
    const root = $("detail"), row = state.trace.rounds[state.selected];
    const open = preserve ? new Set([...root.querySelectorAll("details[open]")].map(node => node.dataset.key)) : new Set();
    root.replaceChildren();
    $("round-title").textContent = state.tab === "run" ? "本次运行" : row ? titleFor(row) : "等待第一轮记录";
    document.querySelectorAll("[data-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.tab === state.tab)));
    $("previous").disabled = state.selected <= 0; $("next").disabled = state.selected >= state.trace.rounds.length - 1;
    if (state.tab === "run") root.append(runOverview());
    else if (!row) root.append(el("div", "empty", "尚无模型调用。切换到运行概览，查看启动过程或异常。"));
    else root.append(state.tab === "flow" ? flow(row) : state.tab === "context" ? context(row) : rawEvents(row.events));
    root.querySelectorAll("details").forEach(node => { if (open.has(node.dataset.key)) node.open = true; });
    const hash = new URLSearchParams({run: state.run, step: String(state.selected), tab: state.tab});
    // Some browsers restrict History API use on file:// documents.
    try { history.replaceState(null, "", `#${hash}`); } catch (_) { /* Navigation still works offline. */ }
  }
  function select(index) { state.selected = index; navigation(); renderDetail(); }
  async function get(url) { const response = await fetch(url, {cache:"no-store"}); if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`); return response.json(); }
  async function loadRun(name, preserve = false) {
    const ticket = ++state.loading;
    try {
      const trace = offline || await get(`/api/trace?run=${encodeURIComponent(name)}`);
      if (ticket !== state.loading) return;
      const changed = !preserve || !state.trace || trace.summary.event_count !== state.trace.summary.event_count || JSON.stringify(trace.warnings) !== JSON.stringify(state.trace.warnings);
      state.trace = trace; state.run = name;
      state.selected = Math.min(Math.max(state.selected, 0), Math.max(trace.rounds.length - 1, 0));
      notice(trace.warnings.join("\n"));
      if (changed) { overview(); navigation(); renderDetail(preserve); }
      if (!offline) $("export").href = `/export?run=${encodeURIComponent(name)}`;
    } catch (error) { if (ticket === state.loading) notice(`${error.message}。确认本地查看器仍在运行，然后点击刷新。`); }
  }
  async function refresh(initial = false) {
    try {
      const runs = offline ? [offline.summary] : await get("/api/runs");
      const selector = $("run-select"), previous = state.run; selector.replaceChildren();
      runs.forEach(run => { const option = el("option", "", run.name); option.value = run.name; selector.append(option); });
      if (!runs.length) { notice("没有找到 events.jsonl。先运行 trace_run，或使用 --runs-dir 指向已有记录目录。"); $("round-title").textContent = "还没有运行记录"; return; }
      const name = runs.some(run => run.name === previous) ? previous : runs[0].name;
      selector.value = name;
      await loadRun(name, !initial && name === previous);
    } catch (error) { notice(`${error.message}。确认本地服务仍在运行，然后点击刷新。`); }
  }
  $("run-select").addEventListener("change", event => { state.selected = 0; state.query = ""; $("search").value = ""; loadRun(event.target.value); });
  $("search").addEventListener("input", event => { state.query = event.target.value.trim(); if (state.trace) navigation(); });
  $("previous").addEventListener("click", () => select(Math.max(0, state.selected - 1)));
  $("next").addEventListener("click", () => select(Math.min(state.trace.rounds.length - 1, state.selected + 1)));
  $("refresh").addEventListener("click", () => refresh());
  const tabs = [...document.querySelectorAll("[data-tab]")];
  tabs.forEach(button => button.addEventListener("click", () => { state.tab = button.dataset.tab; if (state.trace) renderDetail(); }));
  document.addEventListener("keydown", event => {
    if (event.altKey && state.trace && ["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); select(Math.max(0, Math.min(state.trace.rounds.length - 1, state.selected + (event.key === "ArrowLeft" ? -1 : 1)))); }
    if (event.target.getAttribute?.("role") === "tab" && ["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); const next = tabs[(tabs.indexOf(event.target) + (event.key === "ArrowLeft" ? tabs.length - 1 : 1)) % tabs.length]; next.focus(); next.click(); }
  });
  const hash = new URLSearchParams(location.hash.slice(1)); state.run = hash.get("run") || ""; state.selected = Number.parseInt(hash.get("step"), 10) || 0;
  if (["flow", "context", "raw", "run"].includes(hash.get("tab"))) state.tab = hash.get("tab");
  if (offline) { $("mode-label").textContent = "离线快照 · 无需联网"; ["auto-label", "refresh", "export"].forEach(id => $(id).hidden = true); $("run-select").disabled = true; }
  else setInterval(() => { if ($("auto-refresh").checked && !document.hidden) refresh(); }, 5000);
  refresh(true);
})();

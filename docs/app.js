/* 民盟知识库 · 静态加密站客户端
   口令 → PBKDF2 派生密钥 → AES-GCM 解密内容包 → 哈希路由渲染 */
(function () {
  "use strict";
  let DATA = null;
  let CAT_COUNTS = {};

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const SWIRL = '<svg class="swirl hero-swirl" width="260" height="260" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3 A13 13 0 0 1 29 16 A10 10 0 0 1 16 26 A7 7 0 0 1 9 16 A4.2 4.2 0 0 1 16 11.8"/></svg>';

  /* ---------- 启动 ---------- */
  function boot() {
    CAT_COUNTS = {};
    Object.values(DATA.pages).forEach((p) => { CAT_COUNTS[p.category] = (CAT_COUNTS[p.category] || 0) + 1; });
    renderSidebar();
    $("#footMeta").textContent = `民盟知识库 · 共 ${DATA.generated_pages} 页结构化知识`;
    $("#app").hidden = false;
    window.addEventListener("hashchange", route);
    $("#searchForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("#searchInput").value.trim();
      location.hash = "#/search?q=" + encodeURIComponent(q);
    });
    route();
  }

  function entCount() {
    const e = DATA.entities || {};
    return (e.persons || []).length + (e.events || []).length;
  }

  function renderSidebar() {
    const order = DATA.category_order.filter((c) => CAT_COUNTS[c]);
    const cats = order.map((c) =>
      `<li><a href="#/c/${encodeURIComponent(c)}" data-cat="${esc(c)}"><span>${esc(c)}</span><span class="cat-n">${CAT_COUNTS[c]}</span></a></li>`
    ).join("");
    $("#sidebar").innerHTML = `
      <p class="side-head">知识分类</p>
      <ul class="cat-list">${cats}</ul>
      <p class="side-head">专题视图</p>
      <ul class="cat-list">
        <li><a href="#/entities"><span>人物·事件·机构·地点</span><span class="cat-n">${entCount()}</span></a></li>
        <li><a href="#/formulations"><span>提法库 / 口径护栏</span><span class="cat-n">${(DATA.formulations||[]).length}</span></a></li>
      </ul>`;
  }

  /* ---------- 路由 ---------- */
  function route() {
    const h = location.hash.replace(/^#/, "") || "/";
    const view = $("#view");
    window.scrollTo(0, 0);
    let m;
    if (h === "/" || h === "") return renderHome(view);
    if ((m = h.match(/^\/c\/(.+)$/))) return renderCategory(view, decodeURIComponent(m[1]));
    if ((m = h.match(/^\/p\/(.+)$/))) return renderPage(view, decodeURIComponent(m[1]));
    if (h.indexOf("/search") === 0) {
      const q = (h.split("q=")[1] || "");
      return renderSearch(view, decodeURIComponent(q));
    }
    if (h === "/entities") return renderEntities(view);
    if (h === "/formulations") return renderFormulations(view);
    renderHome(view);
  }

  /* ---------- 各视图 ---------- */
  function renderHome(view) {
    const order = DATA.category_order.filter((c) => CAT_COUNTS[c]);
    const cards = order.map((c) =>
      `<a class="cat-card" href="#/c/${encodeURIComponent(c)}">
        <div class="cc-top"><span class="cc-name">${esc(c)}</span><span class="cc-n">${CAT_COUNTS[c]}</span></div>
        <p class="cc-desc">${esc(DATA.category_desc[c] || "")}</p></a>`
    ).join("");
    const recent = Object.values(DATA.pages).slice()
      .sort((a, b) => (b.last_compiled || "").localeCompare(a.last_compiled || "")).slice(0, 8);
    const recentHtml = recent.map((p) =>
      `<li><a href="#/p/${encodeURIComponent(p.slug)}">${esc(p.title)}</a>
        <span class="rl-meta">${esc(p.category)}${p.last_compiled ? " · " + p.last_compiled : ""}${p.needs_review ? ' · <span class="badge-review">待校订</span>' : ""}</span></li>`
    ).join("");
    const ents = DATA.entities || {};
    view.innerHTML = `
      <section class="hero">${SWIRL}
        <p class="eyebrow">上海民盟 · 研究知识底座</p>
        <h1>民盟知识库</h1>
        <p class="hero-sub">面向民盟、上海民盟、统一战线与盟史研究的结构化知识底座——人物、事件、盟史、履职素材、公众号写法，皆可检索追溯。</p>
        <div class="hero-stats">
          <a class="stat" href="#/entities"><span class="s-num">${(ents.persons||[]).length}</span><span class="s-lab">核心人物</span></a>
          <a class="stat" href="#/entities"><span class="s-num">${(ents.events||[]).length}</span><span class="s-lab">重要事件</span></a>
          <a class="stat" href="#/formulations"><span class="s-num">${(DATA.formulations||[]).length}</span><span class="s-lab">提法 / 口径</span></a>
          <span class="stat"><span class="s-num">${DATA.generated_pages}</span><span class="s-lab">知识页</span></span>
        </div>
      </section>
      <section class="block"><div class="sec-title"><h2>知识分类</h2><span class="sec-rule"></span></div>
        <div class="cat-grid">${cards}</div></section>
      <section class="block"><div class="sec-title"><h2>最近编译</h2><span class="sec-rule"></span></div>
        <ul class="recent-list">${recentHtml}</ul></section>`;
  }

  function renderCategory(view, cat) {
    const items = Object.values(DATA.pages).filter((p) => p.category === cat)
      .sort((a, b) => (a.subdir || "").localeCompare(b.subdir || "") || a.title.localeCompare(b.title));
    if (!items.length) { view.innerHTML = '<p class="empty">该分类暂无页面。</p>'; return; }
    const groups = {};
    items.forEach((p) => { (groups[p.subdir] = groups[p.subdir] || []).push(p); });
    let body = "";
    Object.keys(groups).forEach((sub) => {
      if (sub) body += `<h3 class="sub-head">${esc(sub)}</h3>`;
      body += `<ul class="page-list">${groups[sub].map((p) =>
        `<li><a href="#/p/${encodeURIComponent(p.slug)}">${esc(p.title)}</a><span class="pl-meta">${
          p.needs_review ? '<span class="badge-review">待校订</span>' : ""}${
          p.confidence ? `<span class="badge-conf c-${esc(p.confidence)}">${esc(p.confidence)}</span>` : ""}${
          p.source_count ? `<span class="pl-src">${p.source_count} 源</span>` : ""}</span></li>`).join("")}</ul>`;
    });
    view.innerHTML = `<nav class="crumb"><a href="#/">首页</a> / ${esc(cat)}</nav>
      <h1 class="page-h1">${esc(cat)} <span class="h1-n">${items.length} 页</span></h1>
      <p class="page-lede">${esc(DATA.category_desc[cat] || "")}</p>${body}`;
  }

  function renderPage(view, slug) {
    const p = DATA.pages[slug];
    if (!p) { view.innerHTML = '<p class="empty">页面不存在。</p>'; return; }
    const badges = [
      p.needs_review ? '<span class="badge-review">待校订</span>' : "",
      p.confidence ? `<span class="badge-conf c-${esc(p.confidence)}">置信 ${esc(p.confidence)}</span>` : "",
      p.source_count ? `<span class="badge-soft">${p.source_count} 篇来源</span>` : "",
      p.last_compiled ? `<span class="badge-soft">编译 ${esc(p.last_compiled)}</span>` : "",
    ].join("");
    const tags = (p.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
    view.innerHTML = `
      <nav class="crumb"><a href="#/">首页</a> / <a href="#/c/${encodeURIComponent(p.category)}">${esc(p.category)}</a>${p.subdir ? " / " + esc(p.subdir) : ""}</nav>
      <article class="doc">
        <div class="doc-badges">${badges}</div>
        ${tags ? `<div class="doc-tags">${tags}</div>` : ""}
        <div class="doc-body">${p.html}</div>
      </article>`;
  }

  // 从 html 剥标签得纯文本（缓存到页对象，供检索）
  function pageText(p) {
    if (p._text == null) {
      const d = document.createElement("div");
      d.innerHTML = p.html || "";
      p._text = p.title + "\n" + (d.textContent || "");
    }
    return p._text;
  }

  function renderSearch(view, q) {
    $("#searchInput").value = q;
    let results = [];
    if (q) {
      const ql = q.toLowerCase();
      Object.values(DATA.pages).forEach((p) => {
        const txt = pageText(p);
        const hay = txt.toLowerCase();
        const idx = hay.indexOf(ql);
        if (idx >= 0) {
          const score = (p.title.toLowerCase().indexOf(ql) >= 0 ? 3 : 0) + (hay.split(ql).length - 1);
          const start = Math.max(0, idx - 30);
          const snippet = txt.slice(start, start + 160).replace(/\n/g, " ");
          results.push({ p, score, snippet });
        }
      });
      results.sort((a, b) => b.score - a.score);
      results = results.slice(0, 50);
    }
    const hl = (txt) => esc(txt).replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig"), (m) => `<mark>${m}</mark>`);
    const list = results.map(({ p, snippet }) =>
      `<li><a class="sr-title" href="#/p/${encodeURIComponent(p.slug)}">${esc(p.title)}</a><span class="sr-cat">${esc(p.category)}</span>
        <p class="sr-snippet">…${q ? hl(snippet) : esc(snippet)}…</p></li>`).join("");
    view.innerHTML = `<h1 class="page-h1">检索</h1>
      <form class="search-form" id="bigSearch"><input id="bigSearchInput" type="search" value="${esc(q)}" placeholder="人物、事件、提法、盟史关键词…"><button type="submit">检索</button></form>
      ${q ? `<p class="search-stat">「${esc(q)}」共 ${results.length} 条结果${results.length >= 50 ? "（仅显示前 50）" : ""}</p>` : ""}
      <ul class="search-list">${list}</ul>
      ${q && !results.length ? '<p class="empty">没有匹配的页面，换个关键词试试。</p>' : ""}`;
    const f = $("#bigSearch");
    if (f) f.addEventListener("submit", (e) => { e.preventDefault(); location.hash = "#/search?q=" + encodeURIComponent($("#bigSearchInput").value.trim()); });
  }

  function renderEntities(view) {
    const e = DATA.entities || {};
    const kinds = [["persons", "核心人物"], ["events", "重要事件"], ["orgs", "机构组织"], ["places", "地点"]];
    let body = "";
    kinds.forEach(([k, label]) => {
      const rows = e[k] || [];
      if (!rows.length) return;
      const cards = rows.map((x) =>
        `<div class="ent-card"><div class="ent-name">${esc(x.name)}${(x.aliases && x.aliases.length) ? `<span class="ent-alias">${esc(x.aliases.join(" · "))}</span>` : ""}</div>
          ${x.summary ? `<p class="ent-sum">${esc(x.summary)}</p>` : ""}
          ${x.disputes ? `<p class="ent-disp">⚠ ${esc(x.disputes)}</p>` : ""}</div>`).join("");
      body += `<section class="block"><div class="sec-title"><h2>${label} <span class="h1-n">${rows.length}</span></h2><span class="sec-rule"></span></div><div class="ent-grid">${cards}</div></section>`;
    });
    view.innerHTML = `<h1 class="page-h1">实体库</h1>
      <p class="page-lede">从语料抽取的核心人物、事件、机构、地点。带争议/待核标记的条目需回原文与权威资料核对。</p>${body}`;
  }

  function renderFormulations(view) {
    const rows = DATA.formulations || [];
    const cards = rows.map((f) =>
      `<div class="formu-card"><div class="fc-head"><span class="fc-term">${esc(f.term)}</span>${f.status ? `<span class="fc-status">${esc(f.status)}</span>` : ""}</div>
        ${f.canonical ? `<p class="fc-canon"><b>规范表述：</b>${esc(f.canonical)}</p>` : ""}
        ${(f.variants && f.variants.length) ? `<p class="fc-var"><b>变体：</b>${f.variants.map((v) => `<span class="var-chip">${esc(v)}</span>`).join("")}</p>` : ""}
        <p class="fc-meta">${f.first_seen ? "首见 " + esc(f.first_seen) : ""}${f.latest_source ? " · 来源 " + esc(f.latest_source) : ""}</p>
        ${f.note ? `<p class="fc-note">${esc(f.note)}</p>` : ""}</div>`).join("");
    view.innerHTML = `<h1 class="page-h1">提法库 · 口径护栏</h1>
      <p class="page-lede">规范提法、变体与现行状态。涉及正式政治概念（如"章程"等）严格按权威口径，基层共建/协作文件用中性低位阶命名。</p>
      <div class="formu-list">${cards}</div>`;
  }

  /* ---------- 加载明文内容包 ---------- */
  fetch("content.json")
    .then((r) => r.json())
    .then((data) => { DATA = data; boot(); })
    .catch(() => {
      document.body.innerHTML = '<p style="padding:40px;font-family:sans-serif">内容加载失败，请刷新重试。</p>';
    });
})();

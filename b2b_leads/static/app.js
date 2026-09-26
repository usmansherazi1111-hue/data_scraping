"use strict";

const STAGES = ["new", "contacted", "engaged", "qualified", "proposal", "won", "lost"];
const $ = (sel) => document.querySelector(sel);

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const msg = data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    toast(msg);
    throw new Error(msg);
  }
  return data;
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.value === "") continue;
    else if (el.type === "number") out[el.name] = Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

function table(el, cols, rows, onClick) {
  const head = "<tr>" + cols.map((c) => `<th>${esc(c.label)}</th>`).join("") + "</tr>";
  const body = rows.length
    ? rows.map((r, i) => `<tr class="${onClick ? "click" : ""}" data-i="${i}">` +
        cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : esc(c.get ? c.get(r) : r[c.key])}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${cols.length}" class="hint">Nothing here yet</td></tr>`;
  el.innerHTML = head + body;
  if (onClick) el.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => onClick(rows[tr.dataset.i])));
}

const pct = (v) => (v === null || v === undefined ? "–" : `${v}%`);
const tile = (label, value) => `<div class="tile"><div class="v">${esc(value)}</div><div class="l">${esc(label)}</div></div>`;
const pill = (text, kind) => `<span class="pill ${kind || ""}">${esc(text)}</span>`;

// ---------------------------------------------------------------- tabs

const loaders = {};
document.querySelectorAll("nav button").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
    btn.classList.add("active");
    $("#" + btn.dataset.tab).classList.add("active");
    loaders[btn.dataset.tab]?.();
  })
);

// ------------------------------------------------------------ dashboard

loaders.dashboard = async () => {
  const k = await api("/api/kpis");
  const c = k.campaigns;
  $("#kpi-campaigns").innerHTML = [
    tile("Sent", c.sent), tile("Open rate", pct(c.open_rate)), tile("Reply rate", pct(c.reply_rate)),
    tile("Conversion rate", pct(c.conversion_rate)), tile("Bounce rate", pct(c.bounce_rate)),
    tile("Unsubscribe rate", pct(c.unsubscribe_rate)),
  ].join("");
  const p = k.pipeline;
  $("#kpi-pipeline").innerHTML = [
    tile("Contacts", p.contacts), tile("Qualified or better", pct(p.lead_to_qualified_rate)),
    tile("Win rate", pct(p.win_rate)), tile("Opted out", p.opted_out),
  ].join("");
  $("#kpi-stages").innerHTML = STAGES.map((s) => `<div class="stage"><b>${p.by_stage[s]}</b>${s}</div>`).join("");
  const s = k.scraping;
  $("#kpi-scraping").innerHTML = [
    tile("Scrape success rate", pct(s.success_rate)), tile("Blocked", pct(s.block_rate)),
    tile("Disallowed by robots.txt", pct(s.disallowed_rate)), tile("URLs attempted", s.attempts),
    tile("Records extracted", s.records), tile("Avg seconds / URL", s.avg_duration_seconds),
  ].join("");
  table($("#kpi-per-campaign"), [
    { label: "Campaign", key: "name" }, { label: "Status", key: "status" },
    { label: "Sent", key: "sent", num: true }, { label: "Open", get: (r) => pct(r.open_rate), num: true },
    { label: "Reply", get: (r) => pct(r.reply_rate), num: true }, { label: "Conversion", get: (r) => pct(r.conversion_rate), num: true },
  ], k.per_campaign);
};

// ------------------------------------------------------------- contacts

$("#contact-stage").innerHTML += STAGES.map((s) => `<option>${s}</option>`).join("");

loaders.contacts = async () => {
  const q = encodeURIComponent($("#contact-q").value);
  const stage = $("#contact-stage").value;
  const data = await api(`/api/contacts?q=${q}${stage ? "&stage=" + stage : ""}`);
  table($("#contacts-table"), [
    { label: "Name", key: "full_name" }, { label: "Email", key: "email" }, { label: "Title", key: "title" },
    { label: "Company", key: "company" }, { label: "Stage", key: "stage" },
    { label: "Consent", html: (r) => r.opted_out ? pill("opted out", "bad") : r.do_not_contact ? pill("do not contact", "bad") : pill(r.lawful_basis.replace("_", " "), "good") },
    { label: "Source", key: "source" },
  ], data.items, showContact);
};
$("#contact-search").addEventListener("click", loaders.contacts);

async function showContact(row) {
  const c = await api(`/api/contacts/${row.id}`);
  const panel = $("#contact-detail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <h3>${esc(c.full_name || c.email)}</h3>
    <p>${esc(c.title || "")} ${c.company ? "at " + esc(c.company) : ""}<br>
       ${esc(c.email || "")} ${esc(c.phone || "")}<br>
       <span class="hint">Source: ${esc(c.source || "")} ${esc(c.source_url || "")}</span></p>
    <div class="toolbar">
      <label>Stage <select id="cd-stage">${STAGES.map((s) => `<option ${s === c.stage ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      ${c.opted_out ? pill("Opted out " + (c.opted_out_at || "").slice(0, 10), "bad") : '<button id="cd-optout">Record opt-out</button>'}
      <button id="cd-erase">Erase personal data</button>
    </div>
    <h4>Notes</h4>
    <form id="cd-note" class="grid"><textarea name="body" class="wide" rows="2" placeholder="Add a note" required></textarea><button>Add note</button></form>
    ${c.notes.map((n) => `<p><span class="hint">${esc(n.created_at.slice(0, 16))} ${esc(n.author || "")}</span><br>${esc(n.body)}</p>`).join("")}`;
  $("#cd-stage").addEventListener("change", async (e) => {
    await api(`/api/contacts/${c.id}`, { method: "PATCH", body: { stage: e.target.value } });
    toast("Stage updated");
    loaders.contacts();
  });
  $("#cd-optout")?.addEventListener("click", async () => {
    await api(`/api/contacts/${c.id}/opt-out`, { method: "POST", body: { reason: "manual" } });
    showContact(c); loaders.contacts();
  });
  $("#cd-erase").addEventListener("click", async () => {
    if (!confirm("Erase this person's data? Only a suppression entry for the email is kept.")) return;
    await api(`/api/contacts/${c.id}/erase`, { method: "POST" });
    showContact(c); loaders.contacts();
  });
  $("#cd-note").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api(`/api/contacts/${c.id}/notes`, { method: "POST", body: formData(e.target) });
    showContact(c);
  });
}

$("#contact-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/contacts", { method: "POST", body: formData(e.target) });
  e.target.reset(); toast("Contact saved"); loaders.contacts();
});

// ------------------------------------------------------------ companies

loaders.companies = async () => {
  const rows = await api(`/api/companies?q=${encodeURIComponent($("#company-q").value)}`);
  table($("#companies-table"), [
    { label: "ID", key: "id", num: true }, { label: "Name", key: "name" }, { label: "Domain", key: "domain" },
    { label: "Industry", key: "industry" }, { label: "Location", key: "location" }, { label: "Contacts", key: "contacts", num: true },
  ], rows, showCompany);
};
$("#company-search").addEventListener("click", loaders.companies);

async function showCompany(row) {
  const c = await api(`/api/companies/${row.id}`);
  const panel = $("#company-detail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <h3>${esc(c.name)}</h3>
    <p>${esc(c.description || "")}<br><span class="hint">${esc(c.website || c.domain || "")} · ${esc(c.industry || "")} · ${esc(c.location || "")}</span></p>
    <h4>Contacts</h4>
    <ul>${c.contacts_list.map((x) => `<li>${esc(x.full_name || x.email)} ${esc(x.title || "")} (${esc(x.stage)})</li>`).join("") || "<li class='hint'>None</li>"}</ul>
    <h4>Notes</h4>
    <form id="co-note" class="grid"><textarea name="body" class="wide" rows="2" placeholder="Add a note" required></textarea><button>Add note</button></form>
    ${c.notes.map((n) => `<p><span class="hint">${esc(n.created_at.slice(0, 16))}</span><br>${esc(n.body)}</p>`).join("")}`;
  $("#co-note").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api(`/api/companies/${c.id}/notes`, { method: "POST", body: formData(e.target) });
    showCompany(c);
  });
}

$("#company-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/companies", { method: "POST", body: formData(e.target) });
  e.target.reset(); toast("Company saved"); loaders.companies();
});

// ------------------------------------------------------------ campaigns

loaders.campaigns = async () => {
  const rows = await api("/api/campaigns");
  table($("#campaigns-table"), [
    { label: "Name", key: "name" }, { label: "Status", key: "status" }, { label: "Recipients", key: "recipients", num: true },
    { label: "Sent", key: "sent", num: true }, { label: "Open", get: (r) => pct(r.open_rate), num: true },
    { label: "Reply", get: (r) => pct(r.reply_rate), num: true }, { label: "Conversion", get: (r) => pct(r.conversion_rate), num: true },
  ], rows, showCampaign);
};

async function showCampaign(row) {
  const c = await api(`/api/campaigns/${row.id}`);
  const panel = $("#campaign-detail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <h3>${esc(c.name)} ${pill(c.status)}</h3>
    <p class="hint">From ${esc(c.from_name || "")} &lt;${esc(c.from_email || "")}&gt; · limit ${c.daily_send_limit}/day</p>
    <div class="toolbar">
      <label>Add audience by stage <select id="cp-stage"><option value="">all contactable</option>${STAGES.map((s) => `<option>${s}</option>`).join("")}</select></label>
      <input id="cp-tag" placeholder="tag (optional)">
      <button id="cp-add">Add recipients</button>
      <button id="cp-send" class="primary">Send next batch</button>
      <button id="cp-pause">${c.status === "paused" ? "Resume" : "Pause"}</button>
    </div>
    <p class="hint">Opted-out and do-not-contact people are never added or sent to.</p>
    <div id="cp-preview"></div>
    <table id="cp-recipients"></table>`;
  table($("#cp-recipients"), [
    { label: "Name", key: "name" }, { label: "Email", key: "email" }, { label: "Status", key: "status" },
    { label: "Log event", html: (r) => ["opened", "replied", "converted", "bounced"].map((ev) => `<button data-ev="${ev}" data-id="${r.contact_id}">${ev}</button>`).join(" ") },
  ], c.recipients);
  panel.querySelectorAll("button[data-ev]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/campaigns/${c.id}/events`, { method: "POST", body: { contact_id: Number(b.dataset.id), event: b.dataset.ev } });
    showCampaign(c); loaders.campaigns();
  }));
  if (c.recipients.length) {
    const p = await api(`/api/campaigns/${c.id}/preview/${c.recipients[0].contact_id}`);
    $("#cp-preview").innerHTML = `<h4>Preview</h4><pre>To: ${esc(p.to)}\nSubject: ${esc(p.subject)}\n\n${esc(p.body)}</pre>`;
  }
  $("#cp-add").addEventListener("click", async () => {
    const body = { stage: $("#cp-stage").value || null, tag: $("#cp-tag").value || null };
    const r = await api(`/api/campaigns/${c.id}/recipients`, { method: "POST", body });
    toast(`Added ${r.added} recipients`); showCampaign(c); loaders.campaigns();
  });
  $("#cp-send").addEventListener("click", async () => {
    const r = await api(`/api/campaigns/${c.id}/send`, { method: "POST" });
    toast(`Sent ${r.sent}, skipped ${r.skipped}, failed ${r.failed} (${r.mode === "OutboxSender" ? "dry run to outbox folder" : "SMTP"})`);
    showCampaign(c); loaders.campaigns();
  });
  $("#cp-pause").addEventListener("click", async () => {
    await api(`/api/campaigns/${c.id}/status/${c.status === "paused" ? "active" : "paused"}`, { method: "POST" });
    showCampaign(c); loaders.campaigns();
  });
}

$("#campaign-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/campaigns", { method: "POST", body: formData(e.target) });
  e.target.reset(); toast("Campaign created"); loaders.campaigns();
});

// ------------------------------------------------------------- scraping

const outcomeKind = { success: "good", blocked: "bad", disallowed: "warn" };

loaders.scraping = async () => {
  const rows = await api("/api/scrape-jobs");
  table($("#jobs-table"), [
    { label: "ID", key: "id", num: true }, { label: "Name", key: "name" }, { label: "Status", key: "status" },
    { label: "URLs", key: "total_urls", num: true }, { label: "OK", key: "succeeded", num: true },
    { label: "Failed", key: "failed", num: true }, { label: "Records", key: "records", num: true },
    { label: "CSV", html: (r) => r.has_csv ? `<a href="/api/scrape-jobs/${r.id}/csv" download>Download</a>` : "" },
  ], rows, showJob);
  if (rows.some((r) => r.status === "running" || r.status === "pending")) {
    clearTimeout(loaders.scrapingTimer);
    loaders.scrapingTimer = setTimeout(() => $("#scraping").classList.contains("active") && loaders.scraping(), 3000);
  }
};

async function showJob(row) {
  const j = await api(`/api/scrape-jobs/${row.id}`);
  const panel = $("#job-detail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <h3>${esc(j.name)} ${pill(j.status)}</h3>
    <p>Success rate ${pct(j.kpis.success_rate)} · blocked ${pct(j.kpis.block_rate)} · disallowed ${pct(j.kpis.disallowed_rate)} ${j.error ? "<br>" + esc(j.error) : ""}</p>
    <table id="job-attempts"></table>`;
  table($("#job-attempts"), [
    { label: "URL", key: "url" }, { label: "Outcome", html: (r) => pill(r.outcome, outcomeKind[r.outcome] || "bad") },
    { label: "HTTP", key: "http_status", num: true }, { label: "Tries", key: "tries", num: true },
    { label: "Proxy", key: "proxy" }, { label: "Records", key: "records", num: true }, { label: "Error", key: "error" },
  ], j.attempts);
}

$("#scrape-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = formData(e.target);
  body.urls = body.urls.split(/\s+/).filter(Boolean);
  if (!body.prompt) delete body.prompt;
  await api("/api/scrape-jobs", { method: "POST", body });
  toast("Scrape job started"); loaders.scraping();
});

// -------------------------------------------------------------- proxies

loaders.proxies = async (check = false) => {
  const rows = await api(check ? "/api/proxies/check" : "/api/proxies", { method: check ? "POST" : "GET" });
  table($("#proxies-table"), [
    { label: "Proxy", key: "proxy" },
    { label: "Health", html: (r) => !r.healthy ? pill("unhealthy", "bad") : r.cooling_down ? pill("cooling down", "warn") : pill("healthy", "good") },
    { label: "Success rate", get: (r) => pct(r.success_rate), num: true }, { label: "OK", key: "successes", num: true },
    { label: "Failures", key: "failures", num: true }, { label: "Blocks", key: "blocks", num: true },
    { label: "Latency ms", key: "last_latency_ms", num: true }, { label: "Last error", key: "last_error" },
  ], rows);
};
$("#proxy-check").addEventListener("click", () => loaders.proxies(true));

loaders.dashboard();

/* IHH Referral Intake frontend. Plain JS, no build step. Talks to the API in docs/api-contract.md.
   Steps: 1 Upload -> 2 Check results (three tables, each Confirm / Edit) -> 3 Decide. Plus the bed board. */
"use strict";

const params = new URLSearchParams(location.search);
const DEMO = params.has("demo");
const BASE = params.get("api") || (location.protocol === "file:" ? "http://localhost:8000" : "");
const PROCESSING = ["uploaded", "transcribing", "analyzing"];
const DECIDABLE = ["ready_for_review", "info_requested"];

// ---------- Vocabulary (one place to change wording) ----------
const DECISION_WORD = { approve: "Approve", deny: "Deny", needs_review: "Needs review" };
const REFERRAL_STATUS = {
  uploaded: ["Waiting to start", "b-info"], transcribing: ["Reading pages", "b-info"], analyzing: ["Checking rules", "b-info"],
  ready_for_review: ["Ready for review", "b-warn"], approved: ["Approved", "b-ok"], denied: ["Denied", "b-bad"],
  info_requested: ["Info requested", "b-held"], failed: ["Failed", "b-bad"],
};
const RULE_STATUS = {
  fail: ["Blocks admission", "b-bad"], unknown: ["Not in packet", "b-warn"], needs_human: ["Needs your judgment", "b-held"],
  pass: ["Meets", "b-ok"], not_applicable: ["Does not apply", "b-off"],
};
const REVIEW_KIND = {
  missing_info: "Missing from packet", judgment_call: "Needs your judgment", unverified_evidence: "Quote not found on page",
  conflict: "Pages disagree", no_bed: "No matching bed", county_needed: "County needed",
};
const TREE_NODE = {
  exclusions: "Exclusions", must_rules: "Required criteria", general_eligibility: "General eligibility",
  human_review: "Case-by-case review", missing_info: "Missing information", unverified: "Quote check",
  conflicts: "Contradictions", bed: "Bed",
};
// Bed board: every status is a word, a color and a fill pattern (see .s-* in styles.css).
const BED_STATUS = {
  available: { label: "Available", badge: "b-ok" },
  held: { label: "Held", badge: "b-held" },
  occupied: { label: "Occupied", badge: "b-off" },
  needs_cleaning: { label: "Needs cleaning", badge: "b-warn" },
  out_of_service: { label: "Out of service", badge: "b-off" },
};
// Bed features: add a row here when IHH's sheet gains a column; tiles, filters and the editor pick it up.
const BED_FEATURES = [
  { key: "wheelchair_accessible", short: "Wheelchair", label: "Wheelchair accessible" },
  { key: "ground_floor", short: "Ground floor", label: "Ground floor" },
  { key: "oxygen_ok", short: "Oxygen", label: "Oxygen allowed" },
  { key: "isolation_ok", short: "Isolation", label: "Isolation possible" },
];
const SEX_DESIGNATION = { any: "Any", male: "Men", female: "Women" };
const PROGRAM = { recuperative_care: "Recuperative care", project_roomkey: "Project Roomkey", shelter: "Shelter", other: "Other", unknown: "Program unknown" };

// ---------- State ----------
const fresh = () => ({
  id: null, ref: null, details: null, matches: null, drafts: null, loadError: null,
  confirmed: { bed: false, why: false, patient: false },
  editing: { bed: false, why: false, patient: false },
  snapshot: {},
  chosenBed: null, overrides: {}, patientEdits: {}, critFilter: "all", notes: "",
  deciding: false, decideError: null, countyDraft: "", countyBusy: false, countyError: null,
});
const S = {
  tab: "upload", list: null, listError: null, file: null, uploading: false, uploadError: null, pollTimer: null,
  r: fresh(),
  criteria: null,
  beds: { list: null, facilities: null, summary: null, site: "", status: "", error: null },
};

// ---------- Helpers ----------
class ApiError extends Error { constructor(status, detail) { super(detail); this.status = status; } }

function detailText(body) {
  if (body && typeof body === "object" && "detail" in body) {
    const d = body.detail;
    return Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : String(d);
  }
  return typeof body === "string" && body ? body.slice(0, 300) : "The server returned an error.";
}

async function api(path, { method = "GET", json, form } = {}) {
  if (DEMO && window.Mock.handles(path)) {
    const r = await window.Mock.request(method, path, json !== undefined ? json : form);
    if (r.status >= 400) throw new ApiError(r.status, detailText(r.body));
    return r.body;
  }
  const opts = { method, headers: {} };
  if (json !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(json); }
  if (form) opts.body = form;
  let res;
  try { res = await fetch(BASE + path, opts); }
  catch { throw new ApiError(0, "Cannot reach the server. Start it with: uvicorn app.main:app"); }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, detailText(body));
  return body;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const unknown = () => h("span", { class: "muted" }, "Unknown");
const show = (v) => (v === null || v === undefined || v === "" ? unknown() : String(v));
const yesNo = (v) => (v === true ? "Yes" : v === false ? "No" : null);
const badge = (text, cls) => h("span", { class: `badge ${cls}` }, text);
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function fmtDate(d) {
  if (!d) return null;
  const [y, m, day] = String(d).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function fmtTime(ts) {
  if (!ts) return null;
  const d = new Date(String(ts).endsWith("Z") ? ts : ts + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function coordinatorName() { return document.getElementById("coordinator").value.trim(); }

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h("textarea", {}, text); document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  const old = btn.textContent; btn.textContent = "Copied"; setTimeout(() => (btn.textContent = old), 2000);
}

// Turn "(p.12)" into a button that opens page 12, and "[MOB-3, OTH-2]" into rule-id badges.
function richText(text) {
  const out = [];
  let rest = String(text);
  const ruleTail = rest.match(/\s*\[([A-Z0-9][A-Z0-9-]*(?:\s*,\s*[A-Z0-9][A-Z0-9-]*)*)\]\s*$/);
  let ids = [];
  if (ruleTail) { ids = ruleTail[1].split(/\s*,\s*/); rest = rest.slice(0, ruleTail.index); }
  const re = /\(p\.\s?(\d+)\)/g;
  let last = 0, m;
  while ((m = re.exec(rest))) {
    out.push(rest.slice(last, m.index));
    const page = +m[1];
    out.push(h("button", { class: "btn-link btn", onclick: () => openPage(page) }, `page ${page}`));
    last = m.index + m[0].length;
  }
  out.push(rest.slice(last));
  if (ids.length) out.push(" ", ...ids.map((id) => h("span", { class: "rule-id" }, id + " ")));
  return out;
}

// ---------- Dialog ----------
const dlg = document.getElementById("dialog");
function openDialog(title, ...body) {
  dlg.replaceChildren(
    h("div", { class: "dialog-head" }, h("h2", {}, title), h("button", { class: "btn btn-small", onclick: () => dlg.close("cancel") }, "Close")),
    h("div", { class: "dialog-body" }, ...body),
  );
  if (!dlg.open) dlg.showModal();
}
function confirmDialog(title, message, okLabel, okClass = "btn-primary") {
  return new Promise((resolve) => {
    dlg.returnValue = "";
    openDialog(title,
      h("p", {}, message),
      h("div", { class: "row" },
        h("button", { class: `btn btn-big ${okClass}`, onclick: () => dlg.close("ok") }, okLabel),
        h("button", { class: "btn btn-big", onclick: () => dlg.close("cancel") }, "Go back")));
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

function openPage(page, quote) {
  const id = S.r.id;
  const src = DEMO ? window.Mock.pageImage(page) : `${BASE}/api/referrals/${encodeURIComponent(id)}/pages/${page}/image`;
  const text = h("pre", { class: "transcript" }, "Loading the transcription...");
  const meta = h("p", { class: "muted" });
  openDialog(`Page ${page}`,
    quote ? h("div", { class: "notice notice-info" }, h("p", {}, "Look for this quote: ", h("q", {}, quote))) : null,
    meta,
    h("img", { class: "page-img", src, alt: `Scanned page ${page} of the referral packet` }),
    h("details", {}, h("summary", { class: "btn btn-link" }, "Show the text the AI read from this page"),
      h("p", { class: "small muted" }, "[REDACTED] means removed for privacy. It does not mean the information is missing."), text));
  api(`/api/referrals/${encodeURIComponent(id)}/pages/${page}`)
    .then((p) => { text.textContent = p.markdown; meta.textContent = `${p.doc_type}. Legibility: ${p.legibility}.`; })
    .catch((e) => { text.textContent = e.message; });
}

// ---------- Navigation ----------
function setTab(tab) {
  S.tab = tab;
  const hash = S.r.id && tab !== "beds" ? `#${S.r.id}/${tab}` : `#${tab}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (tab === "upload") loadList();
  if (tab === "beds") loadBeds();
  render();
  document.getElementById("view").focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function renderNav() {
  const hasRef = !!(S.r.ref && !PROCESSING.includes(S.r.ref.status) && S.r.ref.status !== "failed");
  for (const btn of document.querySelectorAll(".step")) {
    const tab = btn.dataset.tab;
    if (tab === S.tab) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
    btn.disabled = (tab === "review" || tab === "decide") && !hasRef;
  }
}

function render() {
  renderNav();
  const view = document.getElementById("view");
  const tabs = { upload: renderUpload, review: renderReview, decide: renderDecide, beds: renderBeds };
  view.replaceChildren(tabs[S.tab]());
}

// ---------- Referral loading ----------
function stopPolling() { clearTimeout(S.pollTimer); S.pollTimer = null; }

async function openReferral(id, want) {
  stopPolling();
  S.r = fresh();
  S.r.id = id;
  try {
    S.r.ref = await api(`/api/referrals/${encodeURIComponent(id)}`);
  } catch (e) { S.uploadError = e.message; render(); return; }
  if (PROCESSING.includes(S.r.ref.status) || S.r.ref.status === "failed") {
    setTab("upload"); if (S.r.ref.status !== "failed") poll(); return;
  }
  await loadAnalysis();
  const decided = !DECIDABLE.includes(S.r.ref.status);
  if (decided) S.r.confirmed = { bed: true, why: true, patient: true };
  setTab(want || (S.r.ref.status === "ready_for_review" ? "review" : "decide"));
}

async function loadAnalysis() {
  const id = encodeURIComponent(S.r.id);
  try {
    const [details, matches, drafts] = await Promise.all([
      api(`/api/referrals/${id}/details`),
      api(`/api/referrals/${id}/bed-matches`).catch(() => null),
      api(`/api/referrals/${id}/drafts`).catch(() => null),
    ]);
    S.r.details = details; S.r.matches = matches; S.r.drafts = drafts; S.r.loadError = null;
    const rec = S.r.ref.recommendation || details.recommendation;
    const decidedBed = S.r.ref.coordinator_decision && S.r.ref.coordinator_decision.bed_id;
    S.r.chosenBed = decidedBed || (rec.held_bed ? rec.held_bed.bed_id : null);
    S.r.countyDraft = (S.r.ref.county && S.r.ref.county.county) || "";
  } catch (e) { S.r.loadError = e.message; }
}

function poll() {
  S.pollTimer = setTimeout(async () => {
    try { S.r.ref = await api(`/api/referrals/${encodeURIComponent(S.r.id)}`); }
    catch (e) { S.uploadError = e.message; }
    const st = S.r.ref && S.r.ref.status;
    if (st && !PROCESSING.includes(st) && st !== "failed") { await openReferral(S.r.id); return; }
    if (S.tab === "upload") render();
    if (PROCESSING.includes(st)) poll();
  }, 2000);
}

async function loadList() {
  try { S.list = await api("/api/referrals"); S.listError = null; }
  catch (e) { S.listError = e.message; }
  if (S.tab === "upload") render();
}

// ---------- Tab 1: Upload ----------
function renderUpload() {
  const ref = S.r.ref;
  const busy = ref && (PROCESSING.includes(ref.status) || ref.status === "failed");
  return h("div", { class: "stack" },
    busy ? renderProcessing(ref) : renderUploadForm(),
    renderReferralList());
}

function renderUploadForm() {
  const input = h("input", {
    type: "file", accept: "application/pdf,.pdf", hidden: true,
    onchange: (e) => { S.file = e.target.files[0] || null; S.uploadError = null; render(); },
  });
  const zone = h("label", {
    class: "dropzone",
    ondragover: (e) => { e.preventDefault(); zone.classList.add("drag"); },
    ondragleave: () => zone.classList.remove("drag"),
    ondrop: (e) => { e.preventDefault(); S.file = e.dataTransfer.files[0] || null; S.uploadError = null; render(); },
  },
  input,
  S.file ? h("span", { class: "file-name" }, S.file.name) : h("span", { class: "file-name" }, "Drop the PDF here"),
  h("span", { class: "btn" }, S.file ? "Choose a different file" : "Choose PDF file"));

  return h("section", { class: "panel" },
    h("h1", {}, "Upload a referral packet"),
    h("p", { class: "muted" }, "Choose the PDF the hospital sent. The system reads every page and checks it against IHH's admission criteria. This takes 2 to 5 minutes."),
    zone,
    S.uploadError ? h("div", { class: "notice notice-bad", role: "alert" }, h("p", {}, S.uploadError)) : null,
    h("div", { class: "row", style: "margin-top:20px" },
      h("button", { class: "btn btn-primary btn-big", disabled: !S.file || S.uploading, onclick: upload },
        S.uploading ? "Sending..." : "Send packet")));
}

async function upload() {
  if (!S.file) return;
  if (!/\.pdf$/i.test(S.file.name)) { S.uploadError = "This file is not a PDF. Choose the PDF the hospital sent."; render(); return; }
  S.uploading = true; S.uploadError = null; render();
  const form = new FormData(); form.append("file", S.file);
  try {
    const ref = await api("/api/referrals", { method: "POST", form });
    S.file = null; S.r = fresh(); S.r.id = ref.id; S.r.ref = ref;
    poll();
  } catch (e) {
    S.uploadError = e.status === 503 ? "The server has no Claude API key, so it cannot read packets. Ask the person running the server to set ANTHROPIC_API_KEY." : e.message;
  }
  S.uploading = false; setTab("upload");
}

function renderProcessing(ref) {
  if (ref.status === "failed") {
    return h("section", { class: "panel" },
      h("h1", {}, "The packet could not be processed"),
      h("div", { class: "notice notice-bad", role: "alert" }, h("p", {}, ref.error || "Unknown error.")),
      h("div", { class: "row" },
        h("button", { class: "btn btn-primary btn-big", onclick: retry }, "Try again"),
        h("button", { class: "btn btn-big", onclick: () => { S.r = fresh(); setTab("upload"); } }, "Upload a different packet")));
  }
  const m = (ref.progress || "").match(/(\d+)\s+of\s+(\d+)/);
  const pct = m ? Math.round((+m[1] / +m[2]) * 100) : null;
  return h("section", { class: "panel", "aria-live": "polite" },
    h("h1", {}, "Reading the packet"),
    h("p", {}, h("strong", {}, ref.filename), "  ", badge(REFERRAL_STATUS[ref.status][0], "b-info")),
    h("p", { class: "verdict-word", style: "font-size:1.5rem" }, ref.progress || "Starting"),
    h("div", { class: "progress-track" }, h("div", { class: `progress-fill ${pct === null ? "indeterminate" : ""}`, style: `width:${pct || 0}%` })),
    h("p", { class: "muted", style: "margin-top:12px" }, "This usually takes 2 to 5 minutes. You can keep this page open; the results open by themselves."));
}

async function retry() {
  try { S.r.ref = await api(`/api/referrals/${encodeURIComponent(S.r.id)}/retry`, { method: "POST" }); poll(); }
  catch (e) { S.uploadError = e.message; }
  render();
}

function renderReferralList() {
  let body;
  if (S.listError) body = h("div", { class: "notice notice-bad" }, h("p", {}, S.listError));
  else if (!S.list) body = h("p", { class: "muted" }, "Loading...");
  else if (!S.list.length) body = h("p", { class: "muted" }, "No referrals yet. Upload a packet above to start.");
  else body = h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Referral", "File", "Status", "AI recommends", "Received", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, S.list.map((r) => h("tr", {},
      h("td", { class: "rule-id" }, r.id),
      h("td", {}, r.filename),
      h("td", {}, badge(...REFERRAL_STATUS[r.status])),
      h("td", {}, r.decision ? DECISION_WORD[r.decision] : unknown()),
      h("td", {}, fmtTime(r.created_at)),
      h("td", {}, h("button", { class: "btn btn-small", onclick: () => openReferral(r.id) }, "Open")))))));
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Recent referrals"), h("button", { class: "btn btn-small", onclick: loadList }, "Refresh")),
    body);
}

// ---------- Tab 2: Check results ----------
// Tables are editable while the referral is open. "info_requested" opens again only when the coordinator asks.
function locked() {
  const st = S.r.ref && S.r.ref.status;
  return !(st === "ready_for_review" || (st === "info_requested" && S.r.reopened));
}
function rec() { return (S.r.ref && S.r.ref.recommendation) || (S.r.details && S.r.details.recommendation); }

function renderReview() {
  if (S.r.loadError) return h("div", { class: "notice notice-bad" }, h("p", {}, S.r.loadError));
  if (!S.r.details) return h("p", {}, "Loading...");
  return h("div", { class: "stack" },
    h("div", {},
      h("h1", {}, `Referral ${S.r.ref.id}`),
      h("p", { class: "muted" }, S.r.ref.filename, ". Received ", fmtTime(S.r.ref.created_at), "."),
      locked() ? h("div", { class: "notice notice-info" }, h("p", {}, "This referral is already decided. The tables are read-only.")) : null),
    renderVerdict(),
    renderBedPanel(),
    renderWhyPanel(),
    renderPatientPanel(),
    h("div", { class: "row" }, h("button", { class: "btn btn-primary btn-big", onclick: () => setTab("decide") }, "Continue to decision")));
}

function renderVerdict() {
  const r = rec();
  const items = [["bed", "Bed"], ["why", "Reasons"], ["patient", "Patient information"]];
  return h("section", { class: "verdict", "aria-label": "AI recommendation" },
    h("div", { class: `verdict-main v-${r.decision}` },
      h("div", { class: "verdict-kicker" }, "The AI recommends"),
      h("div", { class: "verdict-word" }, DECISION_WORD[r.decision]),
      h("p", {}, r.headline),
      S.r.ref.one_liner ? h("p", { class: "muted" }, S.r.ref.one_liner) : null,
      r.top_reasons.length ? h("ul", {}, r.top_reasons.map((t) => h("li", {}, richText(t)))) : null,
      h("p", { class: "verdict-note" }, "This is a recommendation. You make the final decision.")),
    h("div", { class: "rail" },
      h("h3", {}, "Check each table"),
      h("ol", {}, items.map(([k, label], i) => h("li", { class: S.r.confirmed[k] ? "ok" : "" },
        h("span", { class: "tick", "aria-hidden": "true" }, S.r.confirmed[k] ? "✓" : String(i + 1)),
        h("a", { href: `#panel-${k}` }, label),
        h("span", { class: "visually-hidden" }, S.r.confirmed[k] ? " (confirmed)" : " (not checked yet)")))),
      h("p", { class: "small muted" }, allConfirmed() ? "All three are confirmed." : "Press “Confirm” on each table once you have checked it.")));
}

function allConfirmed() { const c = S.r.confirmed; return c.bed && c.why && c.patient; }

// A table panel with the Confirm / Edit buttons from the design brief.
function panel(key, number, title, intro, body, { onSave } = {}) {
  const editing = S.r.editing[key], confirmed = S.r.confirmed[key];
  let state;
  if (editing) state = badge("Editing", "b-warn");
  else if (confirmed) state = badge("Confirmed", "b-ok");
  else state = badge("Not checked yet", "b-off");

  const startEdit = () => {
    S.r.snapshot[key] = JSON.stringify({ chosenBed: S.r.chosenBed, overrides: S.r.overrides, patientEdits: S.r.patientEdits });
    S.r.editing[key] = true; S.r.confirmed[key] = false; render(); scrollToPanel(key);
  };
  const cancel = () => {
    Object.assign(S.r, JSON.parse(S.r.snapshot[key]));
    S.r.editing[key] = false; render(); scrollToPanel(key);
  };
  const confirm = () => {
    if (onSave && onSave() === false) return;
    S.r.editing[key] = false; S.r.confirmed[key] = true; render(); scrollToPanel(nextUnconfirmed() || key);
  };

  let buttons = null;
  if (!locked()) {
    buttons = editing
      ? [h("button", { class: "btn", onclick: cancel }, "Cancel changes"), h("button", { class: "btn btn-ok", onclick: confirm }, "Save and confirm")]
      : [h("button", { class: "btn", onclick: startEdit }, "Edit"),
        confirmed ? null : h("button", { class: "btn btn-ok", onclick: confirm }, "Confirm, all correct")];
  }
  return h("section", { id: `panel-${key}`, class: `panel ${confirmed ? "is-confirmed" : ""} ${editing ? "is-editing" : ""}` },
    h("div", { class: "panel-head" },
      h("h2", {}, h("span", { class: "step-n" }, number), title, state),
      h("div", { class: "row" }, buttons)),
    intro ? h("p", { class: "muted" }, intro) : null,
    body);
}
function nextUnconfirmed() { return ["bed", "why", "patient"].find((k) => !S.r.confirmed[k]); }
function scrollToPanel(key) {
  requestAnimationFrame(() => { const el = document.getElementById(`panel-${key}`); if (el) el.scrollIntoView({ block: "start" }); });
}

// ----- Table 1: bed -----
// Census beds use the same value for room and label ("101-A"), so don't print it twice.
function bedName(b, withSite = false) {
  const name = b.room && b.room !== b.label ? `Room ${b.room}, bed ${b.label}` : `Bed ${b.label}`;
  return withSite ? `${b.site}, ${name.toLowerCase()}` : name;
}

function bedOptions() {
  const r = rec();
  const list = S.r.matches || [r.held_bed, ...r.alternative_beds].filter(Boolean);
  if (r.held_bed && !list.some((b) => b.bed_id === r.held_bed.bed_id)) list.unshift(r.held_bed);
  return list;
}

function renderBedPanel() {
  const r = rec();
  const editing = S.r.editing.bed;
  const held = r.held_bed && r.held_bed.bed_id;
  const opts = bedOptions();
  const bedReasons = r.review_reasons.filter((x) => x.kind === "no_bed" || x.kind === "county_needed");

  const table = opts.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, [editing ? "Choose" : null, "Bed", "Facility", "Why it fits", ""].filter(Boolean).map((t) => h("th", {}, t)))),
    h("tbody", {}, opts.map((b) => {
      const chosen = b.bed_id === S.r.chosenBed;
      const pick = () => { S.r.chosenBed = b.bed_id; render(); };
      return h("tr", { class: `${chosen ? "is-chosen" : ""} ${editing ? "clickable" : ""}`, onclick: editing ? pick : null },
        editing ? h("td", {}, h("input", { type: "radio", name: "bed", checked: chosen, "aria-label": `Choose bed ${b.label}`, style: "width:28px;height:28px" })) : null,
        h("td", {}, h("strong", {}, bedName(b))),
        h("td", {}, b.site),
        h("td", {}, b.match_notes.length ? b.match_notes.join(", ") : unknown()),
        h("td", {}, h("div", { class: "row" },
          b.bed_id === held ? badge("Held for this patient", "b-held") : null,
          chosen ? badge("Selected", "b-ok") : null)));
    })))) : null;

  return panel("bed", "1", "Bed",
    editing ? "Pick a different bed if needed. The held bed stays reserved until you decide." : "The system already reserved the best matching bed. It is kept for a few hours while you review.",
    [renderCounty(),
      bedReasons.length ? h("div", { class: "notice notice-warn" }, bedReasons.map((x) => h("p", {}, h("strong", {}, REVIEW_KIND[x.kind] + ": "), x.detail))) : null,
      !held && !opts.length ? h("div", { class: "notice notice-warn" }, h("p", {}, "No bed matches this patient right now. You can still deny or request more information.")) : null,
      table]);
}

function renderCounty() {
  const c = S.r.ref.county;
  const editing = S.r.editing.bed;
  const facilityCounties = (S.beds.facilities || []).map((f) => f.county).filter(Boolean);
  const choices = [...new Set([...(c ? c.candidates : []), ...facilityCounties])];
  const status = !c || !c.county ? badge("Unknown", "b-warn") : c.confirmed ? badge(`Confirmed by ${c.confirmed_by || "staff"}`, "b-ok") : badge("AI suggestion", "b-info");

  const view = h("dl", { class: "kv", style: "margin-bottom:16px" },
    h("dt", {}, "County used for matching"), h("dd", {}, h("div", { class: "row" }, h("strong", {}, show(c && c.county)), status)),
    c && c.basis ? [h("dt", {}, "How we know"), h("dd", {}, c.basis)] : null);
  if (!editing) return view;

  if (!S.beds.facilities) api("/api/facilities").then((f) => { S.beds.facilities = f; if (S.tab === "review") render(); }).catch(() => {});
  const input = h("input", {
    type: "text", list: "county-list", value: S.r.countyDraft, "aria-label": "County",
    oninput: (e) => (S.r.countyDraft = e.target.value),
  });
  return h("div", {}, view,
    h("div", { class: "notice notice-info" },
      h("p", {}, h("strong", {}, "Change the county"), " Beds are only matched in the patient's county. Changing it finds and holds a new bed."),
      h("div", { class: "row" }, input, h("datalist", { id: "county-list" }, choices.map((x) => h("option", { value: x }))),
        h("button", { class: "btn btn-primary", disabled: S.r.countyBusy, onclick: saveCounty }, S.r.countyBusy ? "Finding beds..." : "Use this county")),
      S.r.countyError ? h("p", { class: "why-disabled", role: "alert" }, S.r.countyError) : null));
}

async function saveCounty() {
  const county = S.r.countyDraft.trim();
  if (!county) { S.r.countyError = "Type a county first."; render(); return; }
  if (!coordinatorName()) { S.r.countyError = "Type your name at the top of the page first."; render(); document.getElementById("coordinator").focus(); return; }
  S.r.countyBusy = true; S.r.countyError = null; render();
  try {
    S.r.ref = await api(`/api/referrals/${encodeURIComponent(S.r.id)}/county`, { method: "POST", json: { county, coordinator: coordinatorName() } });
    const keep = { confirmed: S.r.confirmed, editing: S.r.editing, overrides: S.r.overrides, patientEdits: S.r.patientEdits, snapshot: S.r.snapshot };
    await loadAnalysis();
    Object.assign(S.r, keep);
  } catch (e) { S.r.countyError = e.message; }
  S.r.countyBusy = false; render(); scrollToPanel("bed");
}

// ----- Table 2: why -----
function renderWhyPanel() {
  const r = rec();
  const d = S.r.details;
  const conflicts = d.summary.conflicts || [];
  const unverified = r.review_reasons.filter((x) => x.kind === "unverified_evidence");
  const reasons = r.review_reasons.filter((x) => x.kind !== "no_bed" && x.kind !== "county_needed");
  const editing = S.r.editing.why;
  const nOver = Object.keys(S.r.overrides).length;

  const questions = r.hospital_questions.length ? h("div", {},
    h("div", { class: "panel-head", style: "margin:20px 0 8px" }, h("h3", {}, "Questions to ask the hospital"),
      h("button", { class: "btn btn-small", onclick: (e) => copyText(r.hospital_questions.map((q, i) => `${i + 1}. ${q.question}`).join("\n"), e.target) }, "Copy questions")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Rule"), h("th", {}, "Question"))),
      h("tbody", {}, r.hospital_questions.map((q) => h("tr", {}, h("td", { class: "rule-id" }, q.rule_id), h("td", {}, q.question))))))) : null;

  return panel("why", "2", "Why this recommendation",
    editing ? "Change the result of any criterion the AI got wrong. Give a short reason for each change." : "What the AI found, what still needs a person, and every IHH admission criterion with the reason for its result.",
    [
      unverified.length ? h("div", { class: "notice notice-warn" }, h("p", {}, h("strong", {}, "Check these quotes. "),
        "The AI quoted the packet, but the quote could not be found on that page: ", unverified.flatMap((x) => x.rule_ids).join(", "), ".")) : null,
      conflicts.length ? h("div", { class: "notice notice-warn" }, h("p", {}, h("strong", {}, "Pages of the packet disagree")),
        conflicts.map((c) => h("div", { style: "margin-top:10px" },
          h("p", {}, h("strong", {}, c.topic + ". "), c.why_it_matters, " ", c.related_rule_ids.map((x) => h("span", { class: "rule-id" }, x + " "))),
          c.statements.map(evidenceLine)))) : null,
      reasons.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Needs attention"), h("th", {}, "Type"), h("th", {}, "Rules"))),
        h("tbody", {}, reasons.map((x) => h("tr", {}, h("td", {}, x.detail), h("td", {}, REVIEW_KIND[x.kind] || x.kind), h("td", { class: "rule-id" }, x.rule_ids.join(", "))))))) :
        h("div", { class: "notice notice-ok" }, h("p", {}, "Nothing flagged for review.")),
      questions,
      nOver ? h("div", { class: "notice notice-info" }, h("p", {}, `You changed ${nOver} rule result(s). They are sent with your decision.`)) : null,
      S.r.whyError ? h("p", { class: "why-disabled", role: "alert" }, S.r.whyError) : null,
      renderReasoning(editing),
    ], {
      onSave: () => {
        const missing = Object.entries(S.r.overrides).filter(([, o]) => !o.note.trim()).map(([id]) => id);
        S.r.whyError = missing.length ? `Give a reason for your change to ${missing.join(", ")}.` : null;
        if (missing.length) { render(); return false; }
        return true;
      },
    });
}

function evidenceLine(e) {
  return h("div", { class: `evidence ${e.verified ? "" : "unverified"}`, style: "margin-top:6px" },
    h("button", { class: "btn btn-small", onclick: () => openPage(e.page, e.quote) }, `Page ${e.page}`),
    h("div", {}, h("q", {}, e.quote),
      e.verified ? null : h("div", { class: "why-disabled" }, "Not found on this page. Open the page and check.")));
}

// ----- Admission criteria checklist -----
// Every IHH criterion (GET /api/criteria), grouped the way IHH's outline reads, with the AI's result and its reason.
// Groups are matched in this order (first match wins) and shown in DISPLAY_ORDER.
const CRITERIA_GROUPS = [
  { key: "general", title: "General admission criteria", intro: "The patient must meet at least one of these.",
    match: (r) => r.section.startsWith("General admission") },
  { key: "form", title: "Referral form screen", intro: "The checks printed on IHH's referral form.", match: (r) => r.source === "form" },
  { key: "systems", title: "Conditions IHH accepts, by body system", intro: "Shows which accepted conditions match this patient. Most do not apply to any one patient.",
    match: (r) => r.source === "by_system" && r.section.endsWith("(eligible)") },
  { key: "exclude", title: "Exclusions", intro: "If any one of these is present, the patient is not eligible.", match: (r) => r.kind === "exclude" },
  { key: "must", title: "Requirements", intro: "The patient must meet every requirement that applies to them.", match: (r) => r.kind === "must" },
  { key: "conditional", title: "Allowed only with arrangements", intro: "Acceptable only if the arrangement is confirmed before admission.", match: (r) => r.kind === "conditional" },
  { key: "document", title: "Documents the hospital must send", intro: "Blacked-out (redacted) details do not make a document missing.", match: (r) => r.kind === "document" },
  { key: "ask", title: "Questions IHH asks, and notes", intro: "These are not pass or fail on their own.", match: () => true },
];
const DISPLAY_ORDER = ["general", "must", "exclude", "conditional", "document", "systems", "ask", "form"];

// Status is always from the eligibility point of view; say it in words that fit the kind of criterion.
const RESULT_WORDS = {
  must: { pass: ["Met", "b-ok"], fail: ["Not met", "b-bad"] },
  exclude: { pass: ["Not present", "b-ok"], fail: ["Present", "b-bad"] },
  document: { pass: ["In the packet", "b-ok"], fail: ["Missing", "b-bad"] },
  conditional: { pass: ["Arranged or not needed", "b-ok"], fail: ["Cannot be arranged", "b-bad"], unknown: ["Not confirmed yet", "b-warn"] },
  eligible: { pass: ["Matches this patient", "b-ok"], fail: ["Does not match", "b-off"] },
  ask: { not_applicable: ["Answered", "b-info"] },
  note: { not_applicable: ["For your information", "b-info"] },
};
function resultWords(kind, status) {
  const k = RESULT_WORDS[kind] || {};
  if (k[status]) return k[status];
  if (status === "unknown") return ["Can't tell from the packet", "b-warn"];
  if (status === "needs_human") return ["Needs your judgment", "b-held"];
  if (status === "not_applicable") return ["Does not apply", "b-off"];
  return RULE_STATUS[status] || [String(status), "b-off"];
}

function criteriaRules() {
  if (S.criteria) return S.criteria.rules;
  // Fallback when /api/criteria is unreachable: the evaluated rules carry their own text.
  return S.r.details.rule_results.map((x) => ({ id: x.rule_id, text: x.text, kind: x.kind, section: x.section, source: x.source, superseded_by: x.superseded_by }));
}

function criteriaRows() {
  const results = Object.fromEntries(S.r.details.rule_results.map((x) => [x.rule_id, x]));
  return criteriaRules().map((rule) => {
    const res = results[rule.id] || null;
    const ov = S.r.overrides[rule.id];
    const status = ov ? ov.status : res ? res.status : null;
    const attention = !rule.superseded_by && (!res || !!ov || ["fail", "unknown", "needs_human"].includes(status)
      || res.evidence.some((e) => !e.verified));
    return { rule, res, ov, status, attention, group: CRITERIA_GROUPS.find((g) => g.match(rule)).key };
  });
}

function groupVerdict(key, rows) {
  const live = rows.filter((x) => !x.rule.superseded_by);
  const n = (st) => live.filter((x) => x.status === st).length;
  const open = n("unknown") + n("needs_human") + live.filter((x) => !x.res).length;
  if (key === "general") {
    const met = live.filter((x) => x.status === "pass").map((x) => x.rule.id);
    if (met.length) return [`Met (${met.join(", ")})`, "b-ok"];
    return open ? ["Can't tell yet", "b-warn"] : ["None met", "b-bad"];
  }
  const unverified = live.filter((x) => x.res && x.res.evidence.some((e) => !e.verified)).length;
  if (key === "systems") {
    const match = n("pass") ? `${n("pass")} match this patient` : "None match";
    return open ? [`${match}, ${open} to check`, "b-warn"] : [match, n("pass") ? "b-ok" : "b-off"];
  }
  if (key === "ask") return [`${live.length} item(s)`, "b-info"];
  if (n("fail")) return [`${n("fail")} ${{ exclude: "present", document: "missing" }[key] || "not met"}`, "b-bad"];
  if (open) return [`${open} to check`, "b-warn"];
  if (unverified) return [`${unverified} quote(s) to check`, "b-warn"];
  return [{ exclude: "None present", document: "All in the packet", conditional: "All arranged or not needed" }[key] || "All met", "b-ok"];
}

function renderReasoning(editing) {
  const r = rec();
  const tree = h("ol", { class: "tree" }, r.tree.map((t) => h("li", { class: t.outcome },
    h("span", { class: "dot", "aria-hidden": "true" }, t.outcome === "triggered" ? "!" : "✓"),
    h("div", {}, h("strong", {}, TREE_NODE[t.node] || t.node), " ",
      badge(t.outcome === "triggered" ? "Found something" : "Clear", t.outcome === "triggered" ? "b-warn" : "b-ok"),
      h("p", { style: "margin:4px 0 0" }, t.explanation, " ", t.rule_ids.map((x) => h("span", { class: "rule-id" }, x + " ")))))));

  return h("details", { class: "reasoning", open: S.r.reasoningOpen !== false || editing, ontoggle: (e) => (S.r.reasoningOpen = e.target.open) },
    h("summary", {}, "Admission criteria and the AI's reasoning"),
    renderCriteria(editing),
    h("details", { class: "crit-group" }, h("summary", {}, "How the AI reached its recommendation, step by step"),
      h("div", { class: "crit-group-body" }, tree)));
}

function renderCriteria(editing) {
  const all = criteriaRows();
  const filter = S.r.critFilter || "all";
  const nAttention = all.filter((x) => x.attention).length;
  const chips = h("div", { class: "filters", role: "group", "aria-label": "Which criteria to show" },
    [["all", `All criteria (${all.length})`], ["attention", `Only what needs attention (${nAttention})`]].map(([k, label]) =>
      h("button", { class: "chip", "aria-pressed": String(filter === k), onclick: () => { S.r.critFilter = k; render(); } }, label)));

  S.r.openGroups = S.r.openGroups || {};
  const groups = DISPLAY_ORDER.map((key) => {
    const g = CRITERIA_GROUPS.find((x) => x.key === key);
    const rows = all.filter((x) => x.group === key);
    const shown = filter === "attention" ? rows.filter((x) => x.attention) : rows;
    if (!shown.length) return null;
    const [verdict, cls] = groupVerdict(key, rows);
    const isOpen = filter === "attention" || (key in S.r.openGroups ? S.r.openGroups[key] : cls === "b-bad" || cls === "b-warn" || key === "general");
    const intro = key === "form" && S.criteria ? `${g.intro} ${S.criteria.precedence}` : g.intro;
    return h("details", { class: "crit-group", open: isOpen, ontoggle: (e) => (S.r.openGroups[key] = e.target.open) },
      h("summary", {}, h("span", {}, g.title), badge(verdict, cls), h("span", { class: "small muted" }, `${rows.length} criteria`)),
      h("div", { class: "crit-group-body" },
        h("p", { class: "muted small" }, intro),
        h("div", { class: "crit-head", "aria-hidden": "true" }, h("span", {}, "IHH criterion"), h("span", {}, "Result"), h("span", {}, "Why")),
        shown.map((x) => criterionRow(x, editing, key !== "general" && key !== "form"))));
  });
  return h("div", { class: "criteria" },
    h("p", { class: "muted small" }, "Each line shows IHH's own wording, the AI's result, and the reason, with the page it came from."),
    chips,
    groups.some(Boolean) ? groups : h("div", { class: "notice notice-ok" }, h("p", {}, "Nothing needs attention.")));
}

function criterionRow({ rule, res, ov, status }, editing, showSection) {
  let words;
  if (rule.superseded_by) words = [`Replaced by ${rule.superseded_by}`, "b-off"];
  else if (!res) words = ["Not checked", "b-warn"];
  else words = resultWords(rule.kind, status);

  const why = [];
  if (rule.superseded_by) {
    why.push(h("p", {}, "IHH's admission criteria outline takes priority over the referral form here. See ", rule.superseded_by, "."));
  } else if (!res) {
    why.push(h("p", { class: "why-disabled" }, "The AI did not return a result for this criterion. Check it yourself."));
  } else {
    why.push(h("p", {}, res.reasoning, res.confidence === "low" ? h("span", { class: "muted" }, " (the AI is not sure)") : null));
    if (res.answer) why.push(h("p", {}, h("strong", {}, "Answer: "), res.answer));
    if (res.hospital_question) why.push(h("p", {}, h("strong", {}, "Ask the hospital: "), res.hospital_question));
    for (const e of res.evidence) why.push(evidenceLine(e));
    if (ov && !editing) why.push(h("p", {}, h("strong", {}, "Your reason: "), ov.note));
  }

  let override = null;
  if (editing && res && !rule.superseded_by) {
    const sel = h("select", {
      "aria-label": `Change result for ${rule.id}`,
      onchange: (e) => {
        if (e.target.value === "") delete S.r.overrides[rule.id];
        else S.r.overrides[rule.id] = { status: e.target.value, note: (ov && ov.note) || "" };
        render(); document.querySelector(`[data-note="${rule.id}"]`)?.focus();
      },
    }, h("option", { value: "" }, "Keep the AI result"),
    ["pass", "fail", "unknown", "needs_human", "not_applicable"].filter((k) => k !== res.status)
      .map((k) => h("option", { value: k, selected: ov && ov.status === k }, `Change to: ${resultWords(rule.kind, k)[0]}`)));
    override = h("div", { class: "override" }, sel,
      ov ? h("input", { type: "text", "data-note": rule.id, placeholder: "Why? (required)", value: ov.note, "aria-label": `Reason for changing ${rule.id}`, oninput: (e) => (ov.note = e.target.value) }) : h("span"));
  }

  const quiet = (status === "not_applicable" && !ov && rule.kind !== "ask" && rule.kind !== "note") || !!rule.superseded_by;
  return h("div", { class: `crit ${quiet ? "crit-quiet" : ""}` },
    h("div", { class: "crit-rule" },
      h("div", {}, h("span", { class: "rule-id" }, rule.id), showSection ? h("span", { class: "small muted" }, "  ", rule.section) : null),
      h("div", { class: "crit-text" }, rule.text)),
    h("div", { class: "crit-result" }, badge(...words),
      ov && res ? h("div", { class: "edited-mark" }, `Changed by you. The AI said: ${resultWords(rule.kind, res.status)[0]}`) : null),
    h("div", { class: "crit-why" }, why, override));
}

// ----- Table 3: patient -----
const PATIENT_FIELDS = [
  ["one_liner", "Description", (s) => s.one_liner],
  ["age", "Age", (s) => s.age],
  ["sex", "Sex", (s) => (s.sex === "unknown" ? null : cap(s.sex))],
  ["admitting_diagnosis", "Reason for hospital stay", (s) => s.admitting_diagnosis],
  ["housing_situation", "Housing situation", (s) => s.housing_situation],
  ["health_plan", "Health plan", (s) => s.health_plan],
  ["mobility_aid", "Walking aid", (s) => s.bed_needs.mobility_aid],
  ["wheelchair", "Uses a wheelchair", (s) => yesNo(s.bed_needs.wheelchair)],
  ["oxygen", "Needs oxygen", (s) => yesNo(s.bed_needs.oxygen)],
  ["isolation", "Needs isolation", (s) => yesNo(s.bed_needs.isolation)],
  ["bed_notes", "Notes for the bed", (s) => s.bed_needs.notes],
  ["referral_form", "IHH referral form included", (s) => yesNo(s.has_ihh_referral_form)],
];

function renderPatientPanel() {
  const s = S.r.details.summary;
  const editing = S.r.editing.patient;
  const rows = PATIENT_FIELDS.map(([key, label, get]) => {
    const original = get(s);
    const edited = key in S.r.patientEdits;
    let cell;
    if (editing) {
      cell = h("input", { type: "text", value: edited ? S.r.patientEdits[key] : original ?? "", placeholder: "Unknown", "aria-label": label,
        oninput: (e) => {
          const v = e.target.value;
          if (v.trim() === (original ?? "")) delete S.r.patientEdits[key]; else S.r.patientEdits[key] = v;
        } });
    } else if (edited) {
      cell = h("div", {}, h("div", {}, show(S.r.patientEdits[key])),
        h("div", { class: "edited-mark" }, "Changed by you. The packet said: ", original ?? "Unknown"));
    } else cell = show(original);
    return h("tr", {}, h("td", { class: "key" }, label), h("td", {}, cell));
  });

  const docs = s.documents_present.length
    ? s.documents_present.map((d) => h("div", {}, h("span", { class: "rule-id" }, d.doc_id), " on ", d.pages.map((p, i) => [i ? ", " : "", h("button", { class: "btn btn-link", onclick: () => openPage(p) }, `page ${p}`)])))
    : unknown();
  const findings = s.key_findings.length ? h("ul", { style: "margin:0;padding-left:1.1em" }, s.key_findings.map((f) => h("li", {}, richText(f)))) : unknown();

  return panel("patient", "3", "Patient information",
    editing ? "Correct anything that is wrong. Your corrections are saved with your decision as notes. They do not change the AI's analysis." : "What the AI read from the packet. Unknown means the packet does not say.",
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "From the packet"))),
      h("tbody", {}, rows,
        h("tr", {}, h("td", { class: "key" }, "Documents found"), h("td", {}, docs)),
        h("tr", {}, h("td", { class: "key" }, "Key findings"), h("td", {}, findings))))));
}

// ---------- Tab 3: Decide ----------
function decisionNotes() {
  const lines = [];
  if (S.r.notes.trim()) lines.push(S.r.notes.trim());
  const s = S.r.details.summary;
  const edits = PATIENT_FIELDS.filter(([k]) => k in S.r.patientEdits);
  if (edits.length) {
    lines.push("Coordinator corrections to patient information:");
    for (const [k, label, get] of edits) lines.push(`- ${label}: "${get(s) ?? "Unknown"}" -> "${S.r.patientEdits[k]}"`);
  }
  return lines.join("\n");
}

function renderDecide() {
  if (!S.r.details) return h("p", {}, S.r.loadError || "Loading...");
  if (locked()) return renderConfirmation();
  const r = rec();
  const name = coordinatorName();
  const c = S.r.confirmed;
  const badOverride = Object.entries(S.r.overrides).find(([, o]) => !o.note.trim());
  const bed = bedOptions().find((b) => b.bed_id === S.r.chosenBed);

  const block = (needBed) => {
    if (S.r.deciding) return "Sending...";
    if (!name) return "Type your name at the top of the page.";
    if (!c.why || !c.patient) return "Confirm the Reasons and Patient tables first.";
    if (badOverride) return `Give a reason for your change to ${badOverride[0]}.`;
    if (needBed && !c.bed) return "Confirm the Bed table first.";
    if (needBed && !S.r.chosenBed) return "There is no bed to assign.";
    return null;
  };
  const choice = (choiceKey, label, cls, explain, needBed) => {
    const why = block(needBed);
    return h("div", { class: "choice" },
      h("button", { class: `btn btn-big ${cls}`, disabled: !!why, onclick: () => decide(choiceKey) }, label),
      h("p", { class: "small" }, explain),
      why && !S.r.deciding ? h("p", { class: "why-disabled" }, why) : null);
  };

  const checklist = [["bed", "Bed"], ["why", "Reasons"], ["patient", "Patient information"]];
  return h("div", { class: "stack" },
    h("div", {},
      h("h1", {}, "Make the decision"),
      h("p", {}, "The AI recommends ", h("strong", {}, DECISION_WORD[r.decision]), ". You make the final decision.")),
    h("section", { class: "panel" },
      h("h2", { style: "margin-bottom:12px" }, "Before you decide"),
      h("table", {}, h("tbody", {},
        checklist.map(([k, label]) => h("tr", {},
          h("td", { class: "key" }, label),
          h("td", {}, c[k] ? badge("Confirmed", "b-ok") : badge("Not checked yet", "b-off")),
          h("td", {}, c[k] ? null : h("button", { class: "btn btn-small", onclick: () => { setTab("review"); scrollToPanel(k); } }, "Go to table")))),
        h("tr", {}, h("td", { class: "key" }, "Bed to assign"), h("td", { colspan: 2 }, bed ? bedName(bed, true) : unknown())),
        h("tr", {}, h("td", { class: "key" }, "Your name"), h("td", { colspan: 2 }, name || h("span", { class: "why-disabled" }, "Type your name at the top of the page."))),
        Object.keys(S.r.overrides).length ? h("tr", {}, h("td", { class: "key" }, "Rules you changed"), h("td", { colspan: 2 }, Object.keys(S.r.overrides).join(", "))) : null)),
      h("label", { style: "display:block;margin-top:20px" }, h("strong", {}, "Notes (optional)"),
        h("textarea", { value: S.r.notes, oninput: (e) => (S.r.notes = e.target.value), placeholder: "Anything the team should know about this decision" })),
      Object.keys(S.r.patientEdits).length ? h("p", { class: "small muted" }, "Your patient corrections are added to these notes automatically.") : null),
    S.r.decideError ? h("div", { class: "notice notice-bad", role: "alert" }, h("p", {}, S.r.decideError)) : null,
    h("section", { class: "choices", "aria-label": "Your decision" },
      choice("approve", "Approve and assign bed", "btn-ok", "Admits the patient and moves them into the selected bed.", true),
      choice("request_info", "Ask hospital for info", "btn-primary", "Keeps the referral open and drafts a message with the questions. Nothing is sent.", false),
      choice("deny", "Deny referral", "btn-bad", "Releases the held bed and drafts a denial letter. Nothing is sent.", false)));
}

async function decide(choiceKey) {
  const bed = bedOptions().find((b) => b.bed_id === S.r.chosenBed);
  const what = {
    approve: ["Approve this referral?", `The patient will be assigned to ${bed ? bedName(bed, true) : "the held bed"}.`, "Yes, approve", "btn-ok"],
    request_info: ["Ask the hospital for more information?", "A draft message with the questions will be prepared for you to send. The referral stays open.", "Yes, prepare the request", "btn-primary"],
    deny: ["Deny this referral?", "The held bed is released and a denial letter is drafted for you to send.", "Yes, deny", "btn-bad"],
  }[choiceKey];
  if (!(await confirmDialog(...what))) return;

  const held = rec().held_bed && rec().held_bed.bed_id;
  const body = {
    decision: choiceKey,
    coordinator: coordinatorName(),
    notes: decisionNotes(),
    rule_overrides: Object.entries(S.r.overrides).map(([rule_id, o]) => ({ rule_id, status: o.status, note: o.note.trim() })),
    bed_id: choiceKey === "approve" && S.r.chosenBed && S.r.chosenBed !== held ? S.r.chosenBed : null,
  };
  S.r.deciding = true; S.r.decideError = null; render();
  try {
    S.r.ref = await api(`/api/referrals/${encodeURIComponent(S.r.id)}/decision`, { method: "POST", json: body });
    S.r.drafts = await api(`/api/referrals/${encodeURIComponent(S.r.id)}/drafts`).catch(() => null);
  } catch (e) {
    S.r.decideError = e.message;
    if (e.status === 409 && choiceKey === "approve") {
      S.r.decideError += " Pick another bed in the Bed table and confirm it again.";
      S.r.confirmed.bed = false;
      S.r.matches = await api(`/api/referrals/${encodeURIComponent(S.r.id)}/bed-matches`).catch(() => S.r.matches);
    }
  }
  S.r.deciding = false; render(); window.scrollTo(0, 0);
}

function renderConfirmation() {
  const ref = S.r.ref;
  const cd = ref.coordinator_decision;
  const dr = S.r.drafts || {};
  const outcome = {
    approved: ["Approved", "✓", "", "The patient is assigned to the bed below."],
    denied: ["Denied", "✕", "deny", "The held bed was released."],
    info_requested: ["More information requested", "?", "info", "The referral stays open. Send the drafted request, then reopen this referral when the hospital answers."],
  }[ref.status] || [REFERRAL_STATUS[ref.status][0], "•", "", ""];

  const bedId = cd && (cd.bed_id || (cd.decision === "approve" && rec().held_bed && rec().held_bed.bed_id));
  const bed = bedId && bedOptions().find((b) => b.bed_id === bedId);
  const draft = (title, text) => h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, title, badge("Draft, not sent", "b-warn")),
      h("button", { class: "btn btn-small", onclick: (e) => copyText(text, e.target) }, "Copy text")),
    h("pre", { class: "draft" }, text));

  return h("div", { class: "stack" },
    h("section", { class: "panel" },
      h("div", { class: "confirm-hero" },
        h("div", { class: `big-tick ${outcome[2]}`, "aria-hidden": "true" }, outcome[1]),
        h("div", {}, h("h1", {}, outcome[0]), h("p", { style: "margin:4px 0 0" }, outcome[3]))),
      h("dl", { class: "kv", style: "margin-top:24px" },
        h("dt", {}, "Referral"), h("dd", {}, ref.id),
        cd ? [h("dt", {}, "Decided by"), h("dd", {}, cd.coordinator),
          h("dt", {}, "When"), h("dd", {}, fmtTime(cd.decided_at)),
          h("dt", {}, "AI recommended"), h("dd", {}, DECISION_WORD[cd.ai_recommendation], cd.agreed_with_ai ? " (you agreed)" : " (you decided differently)")] : null,
        bedId ? [h("dt", {}, "Bed"), h("dd", {}, bed ? bedName(bed, true) : bedId)] : null,
        dr.kipu_reference ? [h("dt", {}, "Registration number"), h("dd", {}, dr.kipu_reference, " ", badge("Mock Kipu, not a real record", "b-warn"))] : null,
        cd && cd.notes ? [h("dt", {}, "Notes"), h("dd", { style: "white-space:pre-wrap" }, cd.notes)] : null),
      ref.status === "info_requested" ? h("div", { class: "notice notice-info" }, h("p", {}, "When the hospital answers, you can still approve or deny this referral."),
        h("button", { class: "btn", onclick: () => { S.r.reopened = true; S.r.confirmed = { bed: false, why: false, patient: false }; setTab("review"); } }, "Review again")) : null),
    dr.info_request ? draft("Message to the hospital", dr.info_request) : null,
    dr.denial ? draft("Denial letter", dr.denial) : null,
    dr.kipu_payload ? h("details", { class: "panel reasoning" }, h("summary", {}, "Show the mock Kipu record (not sent anywhere)"),
      h("pre", { class: "draft" }, JSON.stringify(dr.kipu_payload, null, 2))) : null,
    h("div", { class: "row" },
      h("button", { class: "btn btn-primary btn-big", onclick: () => { stopPolling(); S.r = fresh(); setTab("upload"); } }, "Start a new referral"),
      h("button", { class: "btn btn-big", onclick: () => setTab("beds") }, "Open the bed board")));
}

// ---------- Beds ----------
async function loadBeds() {
  try {
    const [list, facilities] = await Promise.all([api("/api/beds"), api("/api/facilities")]);
    Object.assign(S.beds, { list, facilities, error: null });
  } catch (e) { S.beds.error = e.message; }
  if (S.tab === "beds") render();
}

// Census beds store "101-A" as both room and label; group by the part before the bed letter.
function roomOf(b) {
  if (b.room && b.room !== b.label) return b.room;
  const m = String(b.label).match(/^(.*?\d)[-\s]?([A-Za-z])$/);
  return m ? m[1] : null;
}

// Bed board, three levels: all sites -> one site's rooms and beds -> one bed (dialog).
function siteGroups() {
  const B = S.beds;
  const known = new Set((B.facilities || []).map((f) => f.id));
  const groups = (B.facilities || []).map((f) => [f, B.list.filter((b) => b.site_id === f.id)]);
  const bySite = {};
  for (const b of B.list.filter((x) => !known.has(x.site_id))) (bySite[b.site] = bySite[b.site] || []).push(b);
  for (const [site, beds] of Object.entries(bySite)) {
    groups.push([{ id: site, name: site, county: beds[0].county, program_type: beds[0].program_type, active: true, synthetic: true }, beds]);
  }
  // Sites that take referrals first, then by name.
  const rank = (f) => (f.active !== false && f.program_type === "recuperative_care" ? 0 : 1);
  return groups.sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].name.localeCompare(b[0].name));
}

function statusCounts(beds) {
  return Object.fromEntries(Object.keys(BED_STATUS).map((k) => [k, beds.filter((b) => b.status === k).length]));
}

function occupancyBar(beds, small) {
  const counts = statusCounts(beds);
  const total = beds.length || 1;
  return h("div", { class: `occupancy ${small ? "occupancy-small" : ""}`, role: "img", "aria-label": Object.entries(counts).filter(([, n]) => n).map(([k, n]) => `${n} ${BED_STATUS[k].label}`).join(", ") || "No beds" },
    Object.entries(counts).filter(([, n]) => n).map(([k, n]) => h("span", { class: `s-${k}`, style: `width:${(n / total) * 100}%` })));
}

function renderBeds() {
  const B = S.beds;
  const head = h("div", { class: "panel-head" },
    h("div", {}, h("h1", {}, "Beds"), h("p", { class: "muted", style: "margin:4px 0 0" },
      B.site ? "Select a bed to see who is in it or to change it." : "Choose a site to see its beds.")),
    h("div", { class: "row" },
      h("label", { class: "btn" }, "Import census (.xlsx)", h("input", { type: "file", accept: ".xlsx", hidden: true, onchange: importCensus })),
      h("button", { class: "btn", onclick: loadBeds }, "Refresh")));
  if (B.error) return h("div", { class: "stack" }, head, h("div", { class: "notice notice-bad" }, h("p", {}, B.error)));
  if (!B.list) return h("div", { class: "stack" }, head, h("p", {}, "Loading the beds..."));

  const groups = siteGroups();
  const current = B.site && groups.find(([f]) => f.id === B.site);
  const scope = current ? current[1] : B.list;
  const counts = statusCounts(scope);
  const legend = h("div", { class: "legend", role: "group", "aria-label": "Show beds by status" },
    Object.entries(BED_STATUS).map(([k, st]) => h("button", {
      class: "chip legend-item", "aria-pressed": String(B.status === k),
      onclick: () => { B.status = B.status === k ? "" : k; render(); },
    }, h("span", { class: `swatch s-${k}`, "aria-hidden": "true" }), h("span", { class: "count" }, counts[k]), st.label)));
  const overview = h("section", { class: "panel" },
    h("h2", { style: "margin-bottom:12px" }, current ? `Bed status (${current[1].length} beds)` : `All sites (${B.list.length} beds)`),
    occupancyBar(scope), legend,
    B.status ? h("p", { class: "small muted", style: "margin-top:8px" }, `Showing only ${BED_STATUS[B.status].label.toLowerCase()} beds. Press it again to show all.`) : null);

  if (current) {
    return h("div", { class: "stack" }, head,
      h("div", {}, h("button", { class: "btn", onclick: () => { B.site = ""; render(); } }, "Back to all sites")),
      overview, renderFacility(...current));
  }
  if (!groups.length) return h("div", { class: "stack" }, head, h("p", { class: "muted" }, "No beds yet. Import the census workbook to add them."));
  return h("div", { class: "stack" }, head, overview,
    h("div", { class: "sites" }, groups.map(([f, beds]) => {
      const n = B.status ? beds.filter((b) => b.status === B.status).length : beds.filter((b) => b.status === "available").length;
      const takes = f.active !== false && f.program_type === "recuperative_care";
      return h("button", { class: "site-card", onclick: () => { B.site = f.id; render(); window.scrollTo(0, 0); } },
        h("span", { class: "site-name" }, f.name),
        h("span", { class: "muted small" }, f.county ? `${f.county} County` : "County unknown", ", ", PROGRAM[f.program_type] || f.program_type),
        occupancyBar(beds, true),
        h("span", { class: "site-count" }, h("strong", {}, n), ` of ${beds.length} ${B.status ? BED_STATUS[B.status].label.toLowerCase() : "available"}`),
        takes ? badge("Takes referrals", "b-ok") : f.active === false ? badge("Inactive", "b-off") : badge("Not matched to referrals", "b-off"));
    })));
}

function renderFacility(f, beds) {
  const shown = S.beds.status ? beds.filter((b) => b.status === S.beds.status) : beds;
  const avail = beds.filter((b) => b.status === "available").length;
  const rooms = new Map();
  for (const b of shown) {
    const r = roomOf(b) || "";
    if (!rooms.has(r)) rooms.set(r, []);
    rooms.get(r).push(b);
  }
  const roomList = [...rooms.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }));
  return h("section", { class: "facility panel" },
    h("div", { class: "facility-head" },
      h("div", {}, h("h2", {}, f.name, f.active === false ? [" ", badge("Inactive", "b-off")] : null),
        h("div", { class: "facility-meta" }, f.county ? `${f.county} County` : "County unknown", ", ", PROGRAM[f.program_type] || f.program_type,
          `. ${avail} of ${beds.length} beds available.`)),
      f.synthetic ? null : h("button", { class: "btn btn-small", onclick: () => editFacility(f) }, "Edit site")),
    f.notes ? h("p", { class: "small muted" }, f.notes) : null,
    shown.length ? h("div", { class: "rooms" }, roomList.map(([room, list]) => h("div", { class: "room" },
      room ? h("p", { class: "room-name" }, `Room ${room}`) : null,
      h("div", { class: "room-beds" }, list.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true })).map(bedTile)))))
      : h("p", { class: "muted" }, "No beds match this filter."));
}

function bedTile(b) {
  const st = BED_STATUS[b.status] || { label: b.status };
  const exit = b.stay && b.stay.expected_exit_date;
  const due = b.status === "occupied" && exit && exit <= todayISO();
  const feats = BED_FEATURES.filter((f) => b[f.key]).map((f) => f.short);
  if (b.sex_designation !== "any") feats.unshift(SEX_DESIGNATION[b.sex_designation]);
  let sub = null;
  if (b.status === "occupied" && exit) sub = due ? `Exit date passed (${fmtDate(exit)})` : `Until ${fmtDate(exit)}`;
  if (b.status === "held") sub = `For ${b.referral_id || "a referral"}`;
  return h("button", { class: `bed s-${b.status} ${due ? "is-due" : ""}`, onclick: () => openBed(b.id), "aria-label": `Bed ${b.label}, ${st.label}${sub ? ", " + sub : ""}` },
    h("span", { class: "bed-label" }, b.label),
    h("span", { class: "bed-status" }, st.label),
    sub ? h("span", { class: "bed-feat" }, sub) : null,
    feats.length ? h("span", { class: "bed-feat" }, feats.join(", ")) : null);
}

async function openBed(id) {
  openDialog("Bed", h("p", {}, "Loading..."));
  let d;
  try { d = await api(`/api/beds/${encodeURIComponent(id)}`); }
  catch (e) { openDialog("Bed", h("div", { class: "notice notice-bad" }, h("p", {}, e.message))); return; }
  const b = d.bed;
  const st = BED_STATUS[b.status] || { label: b.status, badge: "b-off" };
  const busy = b.status === "occupied" || b.status === "held";
  const msg = h("p", { role: "alert" });

  const setStatus = async (status) => {
    try { await api(`/api/beds/${encodeURIComponent(id)}`, { method: "PATCH", json: { status } }); await loadBeds(); openBed(id); }
    catch (e) { msg.className = "why-disabled"; msg.textContent = e.message; }
  };
  const statusButtons = h("div", { class: "row" },
    ["available", "needs_cleaning", "out_of_service"].map((k) => h("button", {
      class: `btn ${b.status === k ? "btn-primary" : ""}`, disabled: busy || b.status === k, onclick: () => setStatus(k),
    }, BED_STATUS[k].label)));

  const form = {};
  const featureEditor = h("div", {},
    BED_FEATURES.map((f) => h("label", { class: "row", style: "min-height:44px" },
      form[f.key] = h("input", { type: "checkbox", checked: b[f.key], style: "width:26px;height:26px" }), f.label)),
    h("label", { class: "row", style: "margin-top:8px" }, h("strong", {}, "Who can use this bed"),
      form.sex_designation = h("select", {}, Object.entries(SEX_DESIGNATION).map(([k, l]) => h("option", { value: k, selected: b.sex_designation === k }, l)))),
    h("label", { style: "display:block;margin-top:12px" }, h("strong", {}, "Notes"), form.notes = h("textarea", { value: b.notes })),
    h("button", { class: "btn btn-primary", style: "margin-top:12px", onclick: async () => {
      const json = { sex_designation: form.sex_designation.value, notes: form.notes.value };
      for (const f of BED_FEATURES) json[f.key] = form[f.key].checked;
      try { await api(`/api/beds/${encodeURIComponent(id)}`, { method: "PATCH", json }); await loadBeds(); openBed(id); }
      catch (e) { msg.className = "why-disabled"; msg.textContent = e.message; }
    } }, "Save bed details"));

  const stay = d.current_stay;
  const stayFields = stay ? [
    ["Name", [stay.first_name, stay.last_name].filter(Boolean).join(" ") || null], ["Client ID", stay.client_id], ["Gender", stay.gender],
    ["Referral", stay.referral_id], ["Intake date", fmtDate(stay.intake_date)], ["Expected exit", fmtDate(stay.expected_exit_date)],
    ["Payer", stay.payer], ["Case manager", stay.case_manager], ["Notes", stay.notes || null],
    ...Object.entries(stay.extra || {}).map(([k, v]) => [k, v === null || v === undefined ? null : String(v)]),
  ] : [];

  const discharge = async () => {
    const who = [stay.first_name, stay.last_name].filter(Boolean).join(" ") || "this person";
    if (!(await confirmDialog("Discharge from this bed?", `${who} will be marked as discharged and bed ${b.label} becomes free.`, "Yes, discharge", "btn-bad"))) { openBed(id); return; }
    try { await api(`/api/assignments/${stay.assignment_id}/discharge`, { method: "POST" }); await loadBeds(); openBed(id); }
    catch (e) { openDialog("Discharge failed", h("div", { class: "notice notice-bad" }, h("p", {}, e.message))); }
  };

  openDialog(`${b.site}, bed ${b.label}`,
    h("div", { class: "row" }, badge(st.label, st.badge),
      b.hold_expires_at ? h("span", {}, `Held for ${b.referral_id} until ${fmtTime(b.hold_expires_at)}`) : null),
    h("dl", { class: "kv" },
      h("dt", {}, "Site"), h("dd", {}, b.site), h("dt", {}, "County"), h("dd", {}, show(b.county)),
      h("dt", {}, "Program"), h("dd", {}, PROGRAM[b.program_type] || b.program_type),
      h("dt", {}, "Who can use it"), h("dd", {}, SEX_DESIGNATION[b.sex_designation]),
      h("dt", {}, "Features"), h("dd", {}, BED_FEATURES.filter((f) => b[f.key]).map((f) => f.label).join(", ") || "None recorded")),
    stay ? h("section", {},
      h("div", { class: "panel-head" }, h("h3", {}, "Current patient"),
        stay.status === "active" ? h("button", { class: "btn btn-bad", onclick: discharge }, "Discharge") : null),
      h("dl", { class: "kv" }, stayFields.map(([k, v]) => [h("dt", {}, k), h("dd", {}, show(v))]))) : h("p", { class: "muted" }, "Nobody is in this bed."),
    h("section", {}, h("h3", { style: "margin-bottom:8px" }, "Change status"),
      busy ? h("p", { class: "small muted" }, "Discharge the patient or release the hold before changing the status.") : null,
      statusButtons, msg),
    h("details", {}, h("summary", { class: "btn btn-link" }, "Edit bed details"), featureEditor),
    d.history.length ? h("details", {}, h("summary", { class: "btn btn-link" }, `Past stays (${d.history.length})`),
      h("table", {}, h("thead", {}, h("tr", {}, ["Who", "Intake", "Expected exit", "Source"].map((t) => h("th", {}, t)))),
        h("tbody", {}, d.history.map((x) => h("tr", {},
          h("td", {}, [x.first_name, x.last_name].filter(Boolean).join(" ") || x.referral_id || "Unknown"),
          h("td", {}, show(fmtDate(x.intake_date))), h("td", {}, show(fmtDate(x.expected_exit_date))), h("td", {}, x.source)))))) : null);
}

function editFacility(f) {
  const county = h("input", { type: "text", value: f.county || "", "aria-label": "County" });
  const program = h("select", { "aria-label": "Program" }, Object.entries(PROGRAM).map(([k, l]) => h("option", { value: k, selected: f.program_type === k }, l)));
  const active = h("input", { type: "checkbox", checked: f.active, style: "width:26px;height:26px" });
  const address = h("input", { type: "text", value: f.address || "", "aria-label": "Address", style: "width:100%" });
  const phone = h("input", { type: "text", value: f.phone || "", "aria-label": "Phone" });
  const notes = h("textarea", { value: f.notes || "", "aria-label": "Notes" });
  const msg = h("p", { role: "alert" });
  openDialog(`Edit ${f.name}`,
    h("p", { class: "muted" }, "Only active recuperative care sites in the patient's county are matched to referrals."),
    h("dl", { class: "kv" },
      h("dt", {}, "County"), h("dd", {}, county), h("dt", {}, "Program"), h("dd", {}, program),
      h("dt", {}, "Taking referrals"), h("dd", {}, h("label", { class: "row" }, active, "Yes, this site is active")),
      h("dt", {}, "Address"), h("dd", {}, address), h("dt", {}, "Phone"), h("dd", {}, phone), h("dt", {}, "Notes"), h("dd", {}, notes)),
    msg,
    h("button", { class: "btn btn-primary btn-big", onclick: async () => {
      try {
        await api(`/api/facilities/${encodeURIComponent(f.id)}`, { method: "PATCH", json: {
          county: county.value.trim() || null, program_type: program.value, active: active.checked,
          address: address.value, phone: phone.value, notes: notes.value } });
        dlg.close(); loadBeds();
      } catch (e) { msg.className = "why-disabled"; msg.textContent = e.message; }
    } }, "Save site"));
}

async function importCensus(e) {
  const file = e.target.files[0];
  if (!file) return;
  openDialog("Importing census", h("p", {}, `Reading ${file.name}...`));
  const form = new FormData(); form.append("file", file);
  try {
    const rep = await api("/api/census/import", { method: "POST", form });
    await loadBeds();
    openDialog("Census imported",
      h("p", {}, `${rep.facilities_created} new site(s).`),
      rep.skipped_sheets.length ? h("p", { class: "muted" }, "Sheets skipped: ", rep.skipped_sheets.join(", ")) : null,
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Sheet", "Beds added", "Beds updated", "Stays added", "Stays closed", "Problems"].map((t) => h("th", {}, t)))),
        h("tbody", {}, rep.sheets.map((s) => h("tr", {},
          h("td", {}, s.sheet), h("td", {}, s.beds_created), h("td", {}, s.beds_updated), h("td", {}, s.stays_created), h("td", {}, s.stays_closed),
          h("td", {}, [...s.skipped, ...s.conflicts].length ? h("ul", { style: "margin:0;padding-left:1.1em" }, [...s.skipped, ...s.conflicts].map((x) => h("li", {}, x))) : "None")))))));
  } catch (err) { openDialog("Census import failed", h("div", { class: "notice notice-bad" }, h("p", {}, err.message))); }
  e.target.value = "";
}

// ---------- Boot ----------
(function boot() {
  const who = document.getElementById("coordinator");
  try { who.value = localStorage.getItem("ihh.coordinator") || ""; } catch { /* storage blocked */ }
  who.addEventListener("input", () => {
    try { localStorage.setItem("ihh.coordinator", who.value); } catch { /* storage blocked */ }
    if (S.tab === "decide") render();
  });
  for (const btn of document.querySelectorAll(".step")) btn.addEventListener("click", () => setTab(btn.dataset.tab));
  if (DEMO) {
    const b = document.getElementById("banner");
    b.hidden = false;
    b.textContent = "Demo mode: referrals are invented sample data. Decisions here do not change real beds.";
  }
  api("/api/facilities").then((f) => (S.beds.facilities = f)).catch(() => {});
  api("/api/criteria").then((c) => { S.criteria = c; if (S.tab === "review") render(); }).catch(() => {});
  // Restore the place after a page refresh: #R-7F3A2C/review or #beds
  const [first, second] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (first && !["upload", "review", "decide", "beds"].includes(first)) openReferral(first, ["review", "decide"].includes(second) ? second : null);
  else setTab(first === "beds" ? "beds" : "upload");
})();

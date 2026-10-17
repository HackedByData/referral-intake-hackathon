/* Demo mode (?demo=1): fakes the /api/referrals routes with INVENTED data so the review flow can be shown
   without an API key or a real packet. Bed routes still go to the real server (POC seed beds).
   Nothing here comes from a real packet. */
(function () {
  const now = () => new Date().toISOString().slice(0, 19);
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const fallbackBeds = [
    { bed_id: "demo-site-a-102-a", site: "Demo Site A", room: "102", label: "A", match_notes: ["male room"] },
    { bed_id: "demo-site-b-1-a", site: "Demo Site B", room: "1", label: "A", match_notes: ["male room", "ground floor"] },
    { bed_id: "demo-site-b-2-c", site: "Demo Site B", room: "2", label: "C", match_notes: ["any-sex room"] },
  ];
  let bedPool = null;

  async function loadBedPool() {
    if (bedPool) return bedPool;
    try {
      const r = await fetch("/api/beds?status=available");
      const beds = r.ok ? await r.json() : [];
      bedPool = beds.slice(0, 4).map((b) => ({
        bed_id: b.id, site: b.site, room: b.room, label: b.label,
        match_notes: [b.sex_designation === "any" ? "any-sex room" : `${b.sex_designation} room`]
          .concat(b.wheelchair_accessible ? ["wheelchair accessible"] : [])
          .concat(b.ground_floor ? ["ground floor"] : []),
      }));
    } catch { bedPool = []; }
    if (bedPool.length < 2) bedPool = fallbackBeds;
    return bedPool;
  }

  const tree = (triggered) => [
    ["exclusions", "No exclusion found with verified evidence."],
    ["must_rules", "No required criterion clearly unmet."],
    ["general_eligibility", "Meets at least one general admission criterion."],
    ["human_review", "No case-by-case judgment needed."],
    ["missing_info", "Information needed to decide is missing from the packet."],
    ["unverified", "All findings are backed by verified quotes."],
    ["conflicts", "Two pages disagree about walking distance."],
    ["bed", "A matching bed was held."],
  ].map(([node, explanation]) => ({
    node, explanation,
    outcome: triggered[node] ? "triggered" : "clear",
    rule_ids: triggered[node] || [],
  }));

  // Invented results for this demo patient. Rule IDs and wording come from the real criteria (GET /api/criteria);
  // every rule without an entry here gets a generic result that says "Demo".
  const SPECIFIC = {
    "GEN-1": { status: "pass", reasoning: "Housing section says the patient has no stable housing, and the H&P says they are too weak to recover outside.",
      evidence: [{ page: 1, quote: "Housing: unsheltered", verified: true }] },
    "MOB-1": { status: "pass", reasoning: "PT note says the patient uses a walker without help.",
      evidence: [{ page: 3, quote: "front wheeled walker, modified independent", verified: true }] },
    "MOB-2": { status: "pass", reasoning: "A PT evaluation is included.", evidence: [{ page: 3, quote: "Physical Therapy Evaluation", verified: true }] },
    "MOB-3": { status: "pass", reasoning: "PT note says the patient walked 150 ft with a walker without help. Page 2 says 20 ft, so check the conflict.",
      evidence: [{ page: 3, quote: "Patient ambulated 150 ft with front wheeled walker, modified independent", verified: true }] },
    "MOB-4": { status: "not_applicable", reasoning: "The patient is not wheelchair dependent." },
    "MH-2": { status: "pass", confidence: "low", reasoning: "Psych screen says the patient denies suicidal thoughts.",
      evidence: [{ page: 1, quote: "Denies SI/HI", verified: false }] },
    "COG-1": { status: "unknown", reasoning: "The packet does not say whether the patient can take medications on their own.",
      hospital_question: "Can the patient self-administer their medications?" },
    "OTH-3": { status: "unknown", reasoning: "IV antibiotics are ordered but home health is not confirmed.",
      evidence: [{ page: 2, quote: "Continue IV antibiotic via PICC x 14 days", verified: true }],
      hospital_question: "Has home health been arranged to maintain the IV and give the antibiotic?" },
    "ID-3": { status: "unknown", reasoning: "IV antibiotics are ordered; no home health order is in the packet.",
      evidence: [{ page: 2, quote: "Continue IV antibiotic via PICC x 14 days", verified: true }],
      hospital_question: "Has home health been ordered for the IV antibiotics?" },
    "INF-1": { status: "unknown", reasoning: "No MRSA swab result appears in the packet.", hospital_question: "Can you send the MRSA nares swab result?" },
    "INT-E1": { status: "pass", reasoning: "Admitted for cellulitis of the left lower leg.", evidence: [{ page: 2, quote: "Left lower extremity cellulitis", verified: true }] },
    "INT-E3": { status: "unknown", reasoning: "IV antibiotics are ordered; home health is not confirmed.",
      evidence: [{ page: 2, quote: "Continue IV antibiotic via PICC x 14 days", verified: true }] },
    "DOC-1": { status: "pass", reasoning: "IHH referral form is on page 1.", evidence: [{ page: 1, quote: "Recuperative Care Referral Form", verified: true }] },
    "DOC-4": { status: "pass", reasoning: "History and physical is on page 2.", evidence: [{ page: 2, quote: "History and Physical", verified: true }] },
    "DOC-7": { status: "pass", reasoning: "Medication record is on page 4.", evidence: [{ page: 4, quote: "Medication Administration Record", verified: true }] },
    "ID-Q1": { status: "not_applicable", answer: "Left lower leg", reasoning: "Stated in the H&P." },
  };
  const NOT_THIS_PATIENT = ["Diabetes", "Cardiac", "Respiratory Illness", "End of life care"];

  function demoResult(rule) {
    const base = { rule_id: rule.id, kind: rule.kind, section: rule.section, source: rule.source, text: rule.text,
      superseded_by: rule.superseded_by || null, human_review: !!rule.human_review,
      evidence: [], answer: null, hospital_question: null, confidence: "high" };
    if (SPECIFIC[rule.id]) return { ...base, ...SPECIFIC[rule.id] };
    if (NOT_THIS_PATIENT.includes(rule.section)) return { ...base, status: "not_applicable", reasoning: `Demo: the patient has no ${rule.section.toLowerCase()} condition.` };
    if (rule.kind === "eligible" || (rule.source === "by_system" && rule.section.endsWith("(eligible)"))) return { ...base, status: "not_applicable", reasoning: "Demo: this condition is not in the packet." };
    if (rule.kind === "exclude") return { ...base, status: "pass", reasoning: "Demo: nothing in the invented packet suggests this." };
    if (rule.kind === "ask") return { ...base, status: "not_applicable", answer: "not stated", reasoning: "Demo: the packet does not answer this." };
    if (rule.kind === "note") return { ...base, status: "not_applicable", reasoning: "Demo: does not apply to this patient." };
    if (rule.kind === "document") return { ...base, status: "pass", reasoning: "Demo: present in the invented packet." };
    if (rule.kind === "conditional") return { ...base, status: "pass", reasoning: "Demo: no arrangement needed for this patient." };
    if (rule.id.startsWith("GEN-")) return { ...base, status: "not_applicable", reasoning: "Demo: GEN-1 already applies." };
    return { ...base, status: "pass", reasoning: "Demo: the invented packet shows the patient meets this." };
  }

  let criteria = null;
  async function loadCriteria() {
    try { const r = await fetch("/api/criteria"); criteria = r.ok ? (await r.json()).rules : []; } catch { criteria = []; }
    if (!criteria.length) {
      criteria = Object.keys(SPECIFIC).map((id) => ({ id, kind: "must", section: "Demo", source: "outline", text: "(criteria text unavailable: server offline)" }));
    }
  }

  function rulesFor(needs) {
    return criteria.filter((r) => !r.superseded_by).map((rule) => {
      const res = demoResult(rule);
      if (needs) return res;
      // The "approve" demo: everything open is resolved and every quote verified.
      return { ...res, status: res.status === "unknown" ? "pass" : res.status, hospital_question: null, confidence: "high",
        reasoning: res.status === "unknown" ? "Demo: confirmed in the packet." : res.reasoning,
        evidence: res.evidence.map((e) => ({ ...e, verified: true })) };
    });
  }

  function summary(sex) {
    return {
      one_liner: "Adult referred after an inpatient stay for left-leg cellulitis; walks with a walker.",
      age: "50s", sex, admitting_diagnosis: "Left lower leg cellulitis", housing_situation: "Unsheltered",
      has_ihh_referral_form: true,
      documents_present: [{ doc_id: "DOC-1", pages: [1] }, { doc_id: "DOC-6", pages: [4] }],
      key_findings: ["Walks 150 ft with a walker (p.3)", "IV antibiotics for 14 days (p.2)", "Daily dressing change (p.2)"],
      conflicts: [{
        topic: "Walking distance",
        statements: [
          { page: 3, quote: "ambulated 150 ft with front wheeled walker", verified: true },
          { page: 2, quote: "ambulates 20 ft with assist", verified: true },
        ],
        related_rule_ids: ["MOB-3"],
        why_it_matters: "MOB-3 needs 100 ft. One page says 150 ft, another says 20 ft.",
      }],
      bed_needs: { sex, wheelchair: false, mobility_aid: "walker", oxygen: null, isolation: null, notes: null },
      health_plan: "CalOptima",
    };
  }

  function makeReferral(id, filename, decision, beds) {
    const needs = decision === "needs_review";
    const rec = {
      decision,
      headline: needs ? "Needs review: 5 item(s) to resolve before deciding" : "Meets IHH admission criteria; bed held",
      top_reasons: needs
        ? ["MRSA swab result is not in the packet [INF-1]", "Home health for IV antibiotics is not confirmed [OTH-3]", "Pages disagree on walking distance [MOB-3]"]
        : ["Walks 150 ft with walker (p.3) [MOB-3]", "Unsheltered and needs recuperative care (p.1) [GEN-1]", "No exclusions found"],
      review_reasons: needs ? [
        { kind: "missing_info", rule_ids: ["INF-1"], detail: "MRSA swab result is not in the packet" },
        { kind: "missing_info", rule_ids: ["OTH-3", "ID-3"], detail: "Home health for IV antibiotics is not confirmed" },
        { kind: "missing_info", rule_ids: ["COG-1"], detail: "The packet does not say if the patient can take their own medications" },
        { kind: "conflict", rule_ids: ["MOB-3"], detail: "Page 2 and page 3 disagree about walking distance" },
        { kind: "unverified_evidence", rule_ids: ["MH-2"], detail: "Quote for MH-2 could not be found on page 1" },
      ] : [],
      hospital_questions: needs ? [
        { rule_id: "INF-1", question: "Can you send the MRSA nares swab result?" },
        { rule_id: "OTH-3", question: "Has home health been arranged to maintain the IV and give the antibiotic?" },
        { rule_id: "COG-1", question: "Can the patient self-administer their medications?" },
      ] : [],
      tree: tree(needs ? { missing_info: ["INF-1", "OTH-3", "ID-3", "COG-1"], conflicts: ["MOB-3"], unverified: ["MH-2"] } : {}),
      held_bed: beds[0],
      alternative_beds: beds.slice(1),
    };
    return {
      view: {
        id, filename, status: "ready_for_review", decision, headline: rec.headline, created_at: now(),
        progress: null, error: null, one_liner: summary("male").one_liner, recommendation: rec, coordinator_decision: null,
        county: { county: "Orange", candidates: [], basis: "Health plan 'CalOptima' (p.1) serves Orange County", confirmed: false, confirmed_by: null },
      },
      details: {
        id, status: "ready_for_review", prompt_version: "demo", model: "claude-opus-5-5",
        summary: needs ? summary("male") : { ...summary("male"), conflicts: [] },
        rule_results: rulesFor(needs),
        recommendation: rec,
        pages: [1, 2, 3, 4].map((p) => ({ page: p, doc_type: ["IHH referral form", "H&P", "PT evaluation", "medication record"][p - 1], orientation: "upright", legibility: "good" })),
        timings: { total: 141.2 },
      },
      drafts: {},
    };
  }

  const store = {};
  let ready = null;
  async function init() {
    const [beds] = await Promise.all([loadBedPool(), loadCriteria()]);
    store["R-DEMO01"] = makeReferral("R-DEMO01", "demo-needs-review.pdf", "needs_review", beds);
    store["R-DEMO02"] = makeReferral("R-DEMO02", "demo-approve.pdf", "approve", beds.slice(1).concat(beds[0]));
  }

  function respond(status, body) { return { status, body }; }

  async function request(method, path, body) {
    ready = ready || init();
    await ready;
    const m = path.match(/^\/api\/referrals(?:\/([^/?]+))?(?:\/(.+))?$/);
    const [, id, sub] = m;
    if (!id) {
      if (method === "GET") return respond(200, Object.values(store).map((r) => r.view).reverse());
      // upload: simulate the pipeline over ~10 s
      const newId = "R-DEMO" + String(Object.keys(store).length + 1).padStart(2, "0");
      const r = makeReferral(newId, (body && body.get && body.get("file") && body.get("file").name) || "upload.pdf", "approve", bedPool);
      r.final = clone(r.view);
      Object.assign(r.view, { status: "uploaded", decision: null, headline: null, recommendation: null, one_liner: null, progress: "Queued" });
      r.started = Date.now();
      store[newId] = r;
      return respond(202, r.view);
    }
    const r = store[id];
    if (!r) return respond(404, { detail: `Referral ${id} not found` });
    if (r.final) {
      const t = (Date.now() - r.started) / 1000;
      if (t > 10) { Object.assign(r.view, r.final); delete r.final; }
      else if (t > 6) Object.assign(r.view, { status: "analyzing", progress: "Checking 155 rules" });
      else Object.assign(r.view, { status: "transcribing", progress: `Transcribing page ${Math.min(4, Math.ceil(t / 1.5))} of 4` });
    }
    if (!sub && method === "GET") return respond(200, r.view);
    if (sub === "details") return r.final ? respond(409, { detail: "Not analyzed yet" }) : respond(200, r.details);
    if (sub === "bed-matches") return respond(200, [r.view.recommendation.held_bed].concat(r.view.recommendation.alternative_beds).filter(Boolean));
    if (sub === "drafts") return respond(200, r.drafts);
    const page = sub && sub.match(/^pages\/(\d+)$/);
    if (page) {
      const info = r.details.pages.find((p) => p.page === +page[1]);
      if (!info) return respond(404, { detail: "Page not found" });
      const quotes = r.details.rule_results.flatMap((x) => x.evidence).filter((e) => e.page === info.page && e.verified).map((e) => e.quote);
      return respond(200, { ...info, markdown: `# ${info.doc_type} (invented demo page)\n\nName: [REDACTED]\n\n${quotes.join("\n\n")}` });
    }
    if (sub === "county" && method === "POST") {
      r.view.county = { ...r.view.county, county: body.county, confirmed: true, confirmed_by: body.coordinator, basis: `Confirmed by ${body.coordinator}` };
      return respond(200, r.view);
    }
    if (sub === "decision" && method === "POST") {
      if (r.view.coordinator_decision) return respond(409, { detail: "This referral was already decided" });
      const status = { approve: "approved", deny: "denied", request_info: "info_requested" }[body.decision];
      const aiMap = { approve: "approve", deny: "deny", request_info: "needs_review" };
      r.view.status = status;
      r.view.coordinator_decision = { ...body, decided_at: now(), ai_recommendation: r.view.decision, agreed_with_ai: aiMap[body.decision] === r.view.decision };
      const bed = body.bed_id || (r.view.recommendation.held_bed && r.view.recommendation.held_bed.bed_id);
      if (body.decision === "approve") r.drafts = { kipu_reference: `MOCK-KIPU-${id}`, kipu_payload: { referral_id: id, bed_id: bed, admitted_by: body.coordinator, note: "MOCK, never sent" } };
      if (body.decision === "deny") r.drafts = { denial: `DRAFT - not sent\n\nThank you for referral ${id}. After review, IHH is unable to admit this patient to recuperative care at this time.\n\nReason: see coordinator notes.\n\n${body.notes || ""}` };
      if (body.decision === "request_info") r.drafts = { info_request: `DRAFT - not sent\n\nRe: referral ${id}\n\nBefore we can decide, please send:\n` + r.view.recommendation.hospital_questions.map((q, i) => `${i + 1}. ${q.question} [${q.rule_id}]`).join("\n") };
      return respond(200, r.view);
    }
    if (sub === "retry") return respond(409, { detail: "Only failed referrals can be retried" });
    return respond(404, { detail: "Not found in demo mode" });
  }

  function pageImage(page) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="850" height="1100"><rect width="100%" height="100%" fill="#fff"/>` +
      `<text x="60" y="110" font-family="sans-serif" font-size="40" fill="#333">Demo page ${page}</text>` +
      `<text x="60" y="170" font-family="sans-serif" font-size="24" fill="#777">Invented placeholder. Real packets show the scanned page here.</text>` +
      Array.from({ length: 22 }, (_, i) => `<rect x="60" y="${230 + i * 36}" width="${500 + ((i * 97) % 230)}" height="12" fill="#e3e3e3"/>`).join("") +
      `<rect x="60" y="320" width="300" height="26" fill="#111"/></svg>`;
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  window.Mock = {
    handles: (path) => path.startsWith("/api/referrals"),
    request,
    pageImage,
  };
})();

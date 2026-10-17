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

  const rules = [
    { rule_id: "MOB-3", status: "pass", confidence: "high", kind: "must", section: "Mobility", source: "outline",
      text: "Patient must be able to ambulate at least 100 ft (rest breaks are acceptable) at modified independence if not wheelchair dependent",
      reasoning: "PT note says the patient walked 150 ft with a walker without help.",
      evidence: [{ page: 3, quote: "Patient ambulated 150 ft with front wheeled walker, modified independent", verified: true }] },
    { rule_id: "MOB-4", status: "unknown", confidence: "medium", kind: "must", section: "Mobility", source: "outline",
      text: "Patient must be able to transfer independently",
      reasoning: "The packet does not describe transfers.", evidence: [],
      hospital_question: "Can the patient get in and out of bed without help?" },
    { rule_id: "INF-1", status: "unknown", confidence: "high", kind: "document", section: "Infection control", source: "outline",
      text: "MRSA nares swab result", reasoning: "No MRSA swab result appears in the packet.", evidence: [],
      hospital_question: "Can you send the MRSA nares swab result?" },
    { rule_id: "OTH-3", status: "unknown", confidence: "medium", kind: "conditional", section: "Other", source: "outline",
      text: "IV antibiotics require home health to maintain the line",
      reasoning: "IV antibiotics are ordered but home health is not confirmed.",
      evidence: [{ page: 2, quote: "Continue IV antibiotic via PICC x 14 days", verified: true }],
      hospital_question: "Has home health been arranged to maintain the IV and give the antibiotic?" },
    { rule_id: "INT-I2", status: "pass", confidence: "high", kind: "exclude", section: "Integumentary (ineligible)", source: "by_system",
      text: "Wounds requiring more than daily dressing changes",
      reasoning: "Dressing changes are once a day.",
      evidence: [{ page: 2, quote: "Dressing change daily to left lower leg", verified: true }] },
    { rule_id: "PSY-2", status: "pass", confidence: "low", kind: "exclude", section: "Behavioral health (ineligible)", source: "by_system",
      text: "Active suicidal ideation",
      reasoning: "Psych screen is negative.",
      evidence: [{ page: 1, quote: "Denies SI/HI", verified: false }] },
    { rule_id: "GEN-1", status: "pass", confidence: "high", kind: "must", section: "General eligibility", source: "outline",
      text: "Adult experiencing homelessness who needs short-term recuperative care",
      reasoning: "Housing section says the patient has no stable housing.",
      evidence: [{ page: 1, quote: "Housing: unsheltered", verified: true }] },
    { rule_id: "DOC-6", status: "pass", confidence: "high", kind: "document", section: "Documents", source: "checklist",
      text: "Medication list", reasoning: "A medication record is present.", evidence: [{ page: 4, quote: "Medication Administration Record", verified: true }] },
    { rule_id: "FORM-A1", status: "not_applicable", confidence: "high", kind: "ask", section: "Referral form", source: "form",
      text: "Does the patient have pets?", reasoning: "Answered on the form.", answer: "not stated", evidence: [] },
  ];

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
      headline: needs ? "Needs review: 3 item(s) to resolve before deciding" : "Meets IHH admission criteria; bed held",
      top_reasons: needs
        ? ["MRSA swab result is not in the packet [INF-1]", "Home health for IV antibiotics is not confirmed [OTH-3]", "Pages disagree on walking distance [MOB-3]"]
        : ["Walks 150 ft with walker (p.3) [MOB-3]", "Unsheltered and needs recuperative care (p.1) [GEN-1]", "No exclusions found"],
      review_reasons: needs ? [
        { kind: "missing_info", rule_ids: ["INF-1"], detail: "MRSA swab result is not in the packet" },
        { kind: "missing_info", rule_ids: ["OTH-3"], detail: "Home health for IV antibiotics is not confirmed" },
        { kind: "conflict", rule_ids: ["MOB-3"], detail: "Page 2 and page 3 disagree about walking distance" },
        { kind: "unverified_evidence", rule_ids: ["PSY-2"], detail: "Quote for PSY-2 could not be found on page 1" },
      ] : [],
      hospital_questions: needs ? [
        { rule_id: "INF-1", question: "Can you send the MRSA nares swab result?" },
        { rule_id: "OTH-3", question: "Has home health been arranged to maintain the IV and give the antibiotic?" },
        { rule_id: "MOB-4", question: "Can the patient get in and out of bed without help?" },
      ] : [],
      tree: tree(needs ? { missing_info: ["INF-1", "OTH-3"], conflicts: ["MOB-3"], unverified: ["PSY-2"] } : {}),
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
        rule_results: needs ? rules : rules.map((r) => ({
          ...r, status: r.status === "unknown" ? "pass" : r.status, hospital_question: null,
          evidence: r.evidence.map((e) => ({ ...e, verified: true })) })),
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
    const beds = await loadBedPool();
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

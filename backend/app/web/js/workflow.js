// Pure data and status logic, no DOM. The stage order comes from the server (GET /stage `chain`,
// app/stage_order.py), so it is filled in by setStageChain(); nothing here hard-codes the workflow.
export const STAGES = [];
export function setStageChain(chain) {
  STAGES.splice(0, STAGES.length, ...chain.map((item) => (item.gate ? [item.stage, item.gate] : [item.stage])));
}
export const GATE_NAMES = {
  G1: "Scope", G2: "Search plan", G3: "Screening", G4: "Extraction", G5: "Gap selection",
  G6: "Framework", G7: "Design", G8: "Analysis plan", G9: "Results", G10: "Draft", G11: "Final sign-off",
};

// mirrors connectors/factory.py SEARCHABLE; the server re-checks
export const SEARCHABLE = ["openalex", "crossref", "semantic_scholar"];

// mirrors app/criteria.py FRAMEWORKS
export const FRAMEWORK_ELEMENTS = {
  pico: ["population", "intervention", "comparison", "outcome"],
  picoc: ["population", "intervention", "comparison", "outcome", "context"],
  spider: ["sample", "phenomenon_of_interest", "design", "evaluation", "research_type"],
  custom: [],
};

// Every screen the workspace builds. Steps without a group ("overview", "guide") sit above the groups; everything
// else is grouped in the left rail. Later spec stages (screening onward) have no screen yet (rule 32) —
// they only appear, locked, in the "Later stages" disclosure built from STAGES.
// Mirrored by app/guidance.py UI_STEPS (a guide entry may only point at a screen listed here).
export const STEPS = [
  { id: "overview", title: "Overview" },
  { id: "guide", title: "Research path" },
  { id: "scope", title: "Scope", group: "Plan" },
  { id: "criteria", title: "Criteria & seeds", group: "Plan" },
  { id: "search", title: "Search", group: "Gather" },
  { id: "results", title: "Results check", group: "Gather" },
  { id: "sources", title: "Sources", group: "Gather" },
  { id: "screen", title: "Screening", group: "Gather" },
  { id: "evidence", title: "Evidence table", group: "Synthesize" },
  { id: "verification", title: "Verify extraction", group: "Synthesize" },
  { id: "ask", title: "Ask", group: "Synthesize" },
  { id: "approvals", title: "Approvals", group: "Project" },
  { id: "team", title: "Team", group: "Project" },
  { id: "activity", title: "Activity", group: "Project" },
];
export const STEP_IDS = new Set(STEPS.map((step) => step.id));
export const GROUPS = ["Plan", "Gather", "Synthesize", "Project"];

export const GUIDES = {
  overview: {
    what: "Where this project stands and what to do next.",
    why: "One place to see progress and anything waiting on you.",
    approval: null,
  },
  guide: {
    what: "What each stage means, which kind of review fits your question, and when meta-analysis is advisable.",
    why: "So you always know what comes next and why.",
    approval: null,
  },
  scope: {
    what: "Write the research question, objectives and key decisions; pick the discipline profile.",
    why: "The agent and every later step work from this.",
    approval: "G1 Scope: owner or supervisor approves.",
  },
  criteria: {
    what: "Set inclusion/exclusion criteria (PICO, PICOC, SPIDER or custom) and add known seed papers.",
    why: "Criteria decide what counts as evidence; seeds test whether the search finds what it should.",
    approval: "Locked once G2 is approved.",
  },
  search: {
    what: "Build Boolean searches from concept blocks and choose databases.",
    why: "A recorded, re-runnable search is what makes a review reproducible.",
    approval: "Searches wait until a person approves G2 Search plan.",
  },
  results: {
    what: "Check PRISMA counts and whether your seed papers were found.",
    why: "A missed seed means the search strategy needs revising before screening.",
    approval: null,
  },
  sources: {
    what: "Review evidence; check metadata automatically or confirm it yourself; merge duplicates.",
    why: "Only verified sources may be cited in answers.",
    approval: null,
  },
  screen: {
    what: "Screen titles and abstracts one record at a time: include, exclude with a reason, or maybe.",
    why: "Your decisions, not the AI's, decide what reaches the included set; every exclusion reason feeds the PRISMA counts.",
    approval: "G3 Screening: owner or supervisor approves the included set.",
  },
  evidence: {
    what: "Compare extracted fields across papers and inspect every populated cell against stored source text.",
    why: "The table keeps each value tied to its quote, page or section, and review status.",
    approval: "Critical fields need a person's verification before G4 can be approved.",
  },
  verification: {
    what: "Compare each extraction with its stored PDF and confirm every critical field.",
    why: "Human checks make sample sizes, measures and effects accountable before synthesis.",
    approval: "G4 opens only after every included paper's current extraction is verified.",
  },
  ask: {
    what: "Ask a research question answered from saved context and sources.",
    why: "Every run records its plan, inputs, model and prompt version.",
    approval: "Answers citing unverified sources fail.",
  },
  approvals: {
    what: "Approve or reject gates, move the project forward, or go back a stage.",
    why: "Nothing passes a gate without a person's decision.",
    approval: "Per gate (shown on each).",
  },
  team: {
    what: "See who is on the project and their role.",
    why: "Roles decide who edits and who approves.",
    approval: "Owner manages members.",
  },
  activity: {
    what: "The append-only audit trail of every change.",
    why: "Accountability: who did what, when, with which model.",
    approval: null,
  },
};

function gate(summary, code) {
  return (summary.gates || []).find((g) => g.code === code);
}

export function stepStatus(stepId, summary) {
  switch (stepId) {
    case "scope": {
      const g1 = gate(summary, "G1");
      const hasQuestion = (summary.context || []).some((item) => item.kind === "question");
      if (g1?.status === "approved") return { status: "done" };
      if (hasQuestion) return { status: "attention" };
      return { status: "todo" };
    }
    case "criteria": {
      const criteria = summary.criteria;
      const seedCount = (summary.seeds || []).length;
      if (criteria?.locked) return { status: "done" };
      if (criteria && criteria.problems?.length === 0) return { status: "attention", count: seedCount || undefined };
      return { status: "todo", count: seedCount || undefined };
    }
    case "search": {
      const g2Approved = gate(summary, "G2")?.status === "approved";
      const anyRun = (summary.searches?.searches || []).some((s) => (s.versions || []).some((v) => v.run_at));
      if (g2Approved && anyRun) return { status: "done" };
      const blocked = (summary.searches?.pending || []).filter((p) => p.blocked_by_gate === "G2").length;
      if (blocked > 0) return { status: "attention", count: blocked };
      return { status: "todo" };
    }
    case "results": {
      const identified = summary.prisma?.identification?.identified || 0;
      const flagged = Boolean(summary.knownItems?.flagged);
      if (identified > 0 && !flagged) return { status: "done" };
      if (flagged) return { status: "attention" };
      return { status: "todo" };
    }
    case "sources": {
      const sources = summary.sources || [];
      if (sources.length === 0) return { status: "todo" };
      const unverified = sources.filter((s) => !s.metadata_verified).length;
      if (unverified === 0) return { status: "done" };
      return { status: "attention", count: unverified };
    }
    case "evidence":
    case "verification":
      return { status: "info" };
    case "ask": {
      const runs = summary.runs || [];
      if (runs.length === 0) return { status: "todo" };
      const latest = runs[0];
      if (latest.status === "completed") return { status: "done" };
      if (["failed", "needs_sources", "needs_configuration"].includes(latest.status)) return { status: "attention" };
      return { status: "todo" };
    }
    case "approvals": {
      const next = summary.stage?.next || [];
      const pendingDecidable = next.filter((option) => {
        const g = option.gate && gate(summary, option.gate);
        return g && g.status === "pending" && g.can_decide;
      }).length;
      const stale = (summary.staleArtifacts || []).length;
      const count = pendingDecidable + (stale > 0 ? stale : 0);
      return count > 0 ? { status: "attention", count } : { status: "info" };
    }
    case "team":
    case "activity":
    case "overview":
      return { status: "info" };
    default:
      return { status: "info" };
  }
}

// The research-path guide (GET /api/guide, app/guides/journey.toml) is the same for every project, so it is
// fetched once and kept in ctx.cache. `request` is passed in to keep this module free of fetch/DOM code.
export async function loadGuide(ctx, request) {
  if (!ctx.cache.guide) ctx.cache.guide = await request("/api/guide");
  return ctx.cache.guide;
}

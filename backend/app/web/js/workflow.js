// Pure data and status logic, no DOM. Mirrors spec Appendix C / app/artifacts.py GATE_STAGES
// (the stage a gate must be approved to reach).
export const STAGES = [
  ["idea"], ["scoped", "G1"], ["search_planned", "G2"], ["retrieved"], ["screened", "G3"], ["extracted", "G4"], ["synthesized"],
  ["gaps_selected", "G5"], ["framework", "G6"], ["design_approved", "G7"], ["data_collected"], ["plan_locked", "G8"],
  ["analyzed", "G9"], ["drafted", "G10"], ["revised"], ["submission_ready", "G11"], ["submitted"], ["revision_loop"], ["accepted"],
];
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

// Every screen the workspace builds. "overview" sits above the groups; everything else is
// grouped in the left rail. Later spec stages (screening onward) have no screen yet (rule 32) —
// they only appear, locked, in the "Later stages" disclosure built from STAGES.
export const STEPS = [
  { id: "overview", title: "Overview" },
  { id: "scope", title: "Scope", group: "Plan" },
  { id: "criteria", title: "Criteria & seeds", group: "Plan" },
  { id: "search", title: "Search", group: "Gather" },
  { id: "results", title: "Results check", group: "Gather" },
  { id: "sources", title: "Sources", group: "Gather" },
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

const ACTION_TEXT = {
  scope: "Write your research question",
  criteria: "Set screening criteria",
  search: "Plan and queue a search",
  results: "Check search results",
  sources: "Verify your sources",
  ask: "Ask the agent",
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

const NEXT_ORDER = ["scope", "criteria", "search", "results", "sources", "ask"];

export function nextAction(summary) {
  for (const id of NEXT_ORDER) {
    if (stepStatus(id, summary).status !== "done") {
      return { id, title: STEPS.find((s) => s.id === id).title, action: ACTION_TEXT[id] };
    }
  }
  return null;
}

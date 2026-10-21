// Wording for the model-usage panel (X.79.1). Pure: no DOM, no network. The server decides the status
// (app/llm_usage.py::budget_status); this only says it in plain words.

const NOTICES = {
  near: (usage) => `Close to the budget: ${usage.percent_used}% used, ${usage.tokens_remaining} tokens left. When it is used up, model calls stop until the owner raises it.`,
  over: () => "The budget is used up. No model calls will be made for this project until the owner raises it.",
};

export function usageSummary(usage) {
  const rows = [["Tokens used", String(usage.tokens_used)]];
  rows.push(["Budget", usage.token_budget === null ? "No limit" : `${usage.token_budget} tokens`]);
  if (usage.token_budget !== null) {
    rows.push(["Remaining", `${usage.tokens_remaining} tokens`], ["Used", `${usage.percent_used}%`]);
  }
  const notice = NOTICES[usage.budget_status];
  return { rows, notice: notice ? { tone: usage.budget_status, text: notice(usage) } : null };
}

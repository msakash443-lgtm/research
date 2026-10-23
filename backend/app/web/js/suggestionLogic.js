// Pure helpers for the shared suggestion panel (X.89); no DOM, so node --test can run them.

// One readable line per piece of evidence: the quote with its page/section, never raw JSON.
export function evidenceLine(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    // A screening suggestion stores its decision as item 0 (screening_suggestions.py); show it as the decision.
    if (item.decision) {
      const head = `Suggested decision: ${item.decision.charAt(0).toUpperCase()}${item.decision.slice(1)}`;
      return item.reason_code ? `${head} (${item.reason_code})` : head;
    }
    const where = item.source_id || item.source || item.id;
    const quote = item.quote || item.text;
    const place = [item.page != null ? `page ${item.page}` : "", item.section ? `section ${item.section}` : ""].filter(Boolean).join(", ");
    const label = [where, place].filter(Boolean).join(", ");
    if (quote) return label ? `${label}: “${quote}”` : `“${quote}”`;
    if (label) return label;
  }
  return "Evidence the panel cannot display.";
}

// "Ask again" needs a server producer for the suggestion's kind. The server says whether one is registered
// (SuggestionRead.can_ask_again, X.98); a card without that answer does not offer the button (X.96).
export function canAskAgain(suggestion) {
  return suggestion.can_ask_again === true;
}

// Keep the pending heading until a card has actually been accepted. Once an accepted card is
// present, avoid describing the whole panel as "not yet yours".
export function suggestionPanelTitle(suggestions) {
  return suggestions.some((suggestion) => suggestion.state === "accepted")
    ? "AI suggestions"
    : "AI suggestion, not yet yours";
}

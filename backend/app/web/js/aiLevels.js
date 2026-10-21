// How far AI may take part in a project (plan X.31.7; values match app/models.py AiInvolvement). One list, so the
// header indicator and the settings panel always word each level the same way.
export const AI_LEVELS = [
  { value: "off", label: "AI off", help: "No text from this project is sent to any AI." },
  {
    value: "suggestions_only",
    label: "AI: suggestions only",
    help: "AI may comment, challenge, find evidence and check structure, but never supplies manuscript words.",
  },
  {
    value: "full",
    label: "AI: full",
    help: "Also allows reworded sentences you may choose to insert, always labelled in the writing record.",
  },
];

export function aiLevel(value) {
  return AI_LEVELS.find((level) => level.value === value) || AI_LEVELS[1];
}

// Q27: only the Full level lets AI wording into the manuscript. A drafted paragraph is AI wording, so it may be
// inserted only then; at Suggestions only it stays on screen as a suggestion.
export function draftInsertAllowed(value) {
  return value === "full";
}

export const AI_ROUTES = [
  { value: "standard", label: "Standard model" },
  { value: "local", label: "Local model only" },
];

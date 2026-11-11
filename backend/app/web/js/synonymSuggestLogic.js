export function synonymSuggestionRequest(projectId, block) {
  return {
    path: `/api/projects/${encodeURIComponent(projectId)}/searches/suggest-synonyms`,
    options: {
      method: "POST",
      body: JSON.stringify({ block }),
    },
  };
}

export function synonymSuggestionPanelOptions(response) {
  return {
    targetKind: "synonym",
    targetId: response.target_id,
  };
}

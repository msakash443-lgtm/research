// Pure helper for the Write step's Export menu (X.31.13). No DOM here, so Node can test it.
// The server builds every file; this only lists the formats and their links.

const FORMATS = [
  ["md", "Markdown"],
  ["docx", "Word (DOCX)"],
  ["json", "JSON"],
  ["bibtex", "BibTeX (verified sources)"],
];

export function manuscriptExportItems(projectId) {
  const base = `/api/projects/${encodeURIComponent(projectId)}/manuscript/export`;
  return FORMATS.map(([format, label]) => ({ format, label, href: `${base}?format=${format}` }));
}

// Row/column orientation of the evidence table (X.63(c)). Pure: no DOM, no network. The server sends papers x
// fields (app/evidence_matrix.py); this only decides which axis is the rows. Cells are passed through untouched.

export const PAPERS_AS_ROWS = "papers";
export const PAPERS_AS_COLUMNS = "fields";

export function normalizeOrientation(value) {
  return value === PAPERS_AS_COLUMNS ? PAPERS_AS_COLUMNS : PAPERS_AS_ROWS;
}

// -> { headers: [{ paper? | field? }], rows: [{ head: { paper? | field? }, cells: [{ paper, field, cell }] }] }
export function tableLayout(matrix, orientation) {
  const entry = (paper, field) => ({ paper, field, cell: paper.cells[field.key] });
  if (normalizeOrientation(orientation) === PAPERS_AS_COLUMNS) {
    return {
      headers: matrix.papers.map((paper) => ({ paper })),
      rows: matrix.fields.map((field) => ({ head: { field }, cells: matrix.papers.map((paper) => entry(paper, field)) })),
    };
  }
  return {
    headers: matrix.fields.map((field) => ({ field })),
    rows: matrix.papers.map((paper) => ({ head: { paper }, cells: matrix.fields.map((field) => entry(paper, field)) })),
  };
}

// Word-level diff for AI wording proposals (M6.8). Pure: no DOM, no network.
// Tokens are words and whitespace runs, so the spacing of the new text is kept exactly.
// Returns segments `{ type: "same" | "del" | "ins", text }` in reading order; deletions come before insertions
// at the same point. Joining the non-"del" segments gives `after`; joining the non-"ins" segments gives `before`.

function tokenize(text) {
  return text.match(/\s+|\S+/g) || [];
}

export function wordDiff(before, after) {
  const a = tokenize(before);
  const b = tokenize(after);
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:].
  const lcs = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const segments = [];
  const push = (type, text) => {
    if (!text) return;
    const last = segments[segments.length - 1];
    if (last && last.type === type) last.text += text;
    else segments.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      push("same", a[i]);
      i += 1;
      j += 1;
    } else if (i < n && (j >= m || lcs[i + 1][j] >= lcs[i][j + 1])) {
      push("del", a[i]);
      i += 1;
    } else {
      push("ins", b[j]);
      j += 1;
    }
  }
  return segments;
}

// Puts a `sep` segment (one space) between a deletion and an insertion that touch, so copied text reads
// "big small" and not "bigsmall". The separator is display-only: `wordDiff` output is unchanged.
export function withSeparators(segments) {
  const out = [];
  for (const segment of segments) {
    const prev = out[out.length - 1];
    if (prev && ((prev.type === "del" && segment.type === "ins") || (prev.type === "ins" && segment.type === "del"))) {
      out.push({ type: "sep", text: " " });
    }
    out.push(segment);
  }
  return out;
}

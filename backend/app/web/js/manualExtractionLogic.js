// Pure validation and payload construction for the Evidence step's human-entry form.
// The API remains the authority for schema/evidence/span checks; this provides immediate, clear feedback.

function parseValue(field, raw) {
  const text = String(raw ?? "").trim();
  if (field.type === "string") return text;
  if (field.type === "integer") {
    if (!/^-?\d+$/.test(text)) throw new Error("must be a whole number");
    return Number(text);
  }
  if (field.type === "number") {
    if (!text || !Number.isFinite(Number(text))) throw new Error("must be a number");
    return Number(text);
  }
  try {
    const value = JSON.parse(text);
    if ((field.type === "list" && !Array.isArray(value)) || (field.type === "object" && (!value || Array.isArray(value) || typeof value !== "object"))) {
      throw new Error(`must be a JSON ${field.type}`);
    }
    return value;
  } catch (error) {
    throw new Error(error.message.startsWith("must be") ? error.message : `must be valid JSON for a ${field.type}`);
  }
}

export function valueShape(spec) {
  if (spec.type === "list") return `JSON list of ${valueShape(spec.items || { type: "string" })}`;
  if (spec.type === "object") return `JSON object: { ${Object.entries(spec.properties || {}).map(([key, value]) => `${key}: ${valueShape(value)}`).join(", ")} }`;
  return spec.type;
}

export function buildManualExtractionPayload({ sourceId, schemaName, fields }) {
  const values = {};
  const evidence = {};
  const problems = [];
  for (const entry of fields) {
    const key = entry.field.key;
    if (entry.notReported) {
      values[key] = null;
      continue;
    }
    const raw = String(entry.value ?? "").trim();
    if (!raw) {
      problems.push(`${key}: enter a value or select Not reported`);
      continue;
    }
    try {
      values[key] = parseValue(entry.field, raw);
    } catch (error) {
      problems.push(`${key}: ${error.message}`);
      continue;
    }
    const quote = String(entry.quote ?? "").trim();
    const page = String(entry.page ?? "").trim();
    const section = String(entry.section ?? "").trim();
    if (!quote) problems.push(`${key}: add the exact supporting quote from the stored PDF`);
    if (!page && !section) problems.push(`${key}: add a page number or section`);
    if (quote && (page || section)) {
      const span = { quote };
      if (page) span.page = /^\d+$/.test(page) ? Number(page) : page;
      if (section) span.section = section;
      evidence[key] = span;
    }
  }
  if (problems.length) throw new Error(problems.join(". "));
  return { source_id: sourceId, schema_name: schemaName, fields: values, evidence };
}

export function manualExtractionError(error) {
  return error?.message || "The manual extraction could not be saved.";
}

export function submitManualExtraction(requestFn, endpoint, payload) {
  return requestFn(endpoint, { method: "POST", body: JSON.stringify(payload) });
}

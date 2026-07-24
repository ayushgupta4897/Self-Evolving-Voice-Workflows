import type { FailureSignature } from "./types";

/**
 * Faithful port of `FailureSignature.to_embedding_text()` in core/schemas.py.
 * Rendered on screen because the interesting property of this string is what it
 * does NOT contain: no utterance, no vertical, no product, no node id. That
 * absence is the reason a patch learned on brake pricing retrieves for a
 * healthcare copay.
 */
export function embeddingText(sig: FailureSignature): string {
  return (
    `A ${sig.node_role} node produced a ${sig.failure_type} failure. ` +
    `A retrieval tool was ${sig.tool_available ? "available" : "not available"} ` +
    `and was ${sig.tool_invoked ? "invoked" : "not invoked"}. ` +
    `The agent ${
      sig.asserted_specific_value
        ? "asserted a specific factual value"
        : "did not assert a specific value"
    }.`
  );
}

/** Port of `FailureSignature.key()`. */
export function signatureKey(sig: FailureSignature): string {
  return [
    sig.failure_type,
    sig.node_role,
    `avail=${sig.tool_available ? 1 : 0}`,
    `inv=${sig.tool_invoked ? 1 : 0}`,
    `spec=${sig.asserted_specific_value ? 1 : 0}`,
  ].join("|");
}

export function humanise(s: string): string {
  return s.replace(/_/g, " ");
}

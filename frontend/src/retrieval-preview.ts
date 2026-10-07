import type { Citation } from "./chunk-types";
import type { Hit } from "./retrieval-types";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function referencePath(hit: Hit) {
  const s = hit.source;
  let path: string;
  if (hit.kind === "log") {
    if (
      !uuid.test(s.experiment_id ?? "") ||
      !uuid.test(s.run_id ?? "") ||
      !Number.isInteger(s.ordinal) ||
      s.ordinal! < 0 ||
      hit.evidence_id !== `lab:${s.run_id}:${s.ordinal}`
    )
      throw new Error("观测锚点不完整。");
    path = `/api/experiments/${s.experiment_id}`;
  } else {
    if (
      !uuid.test(s.chunk_set_id ?? "") ||
      !uuid.test(s.chunk_id ?? "") ||
      hit.evidence_id !== `chunk:${s.chunk_set_id}:${s.chunk_id}`
    )
      throw new Error("原文锚点不完整。");
    path = `/api/chunk-sets/${s.chunk_set_id}/chunks/${s.chunk_id}/citation`;
  }
  if (path !== hit.reference_url) throw new Error("来源路径与固定锚点不一致。");
  return path;
}
interface LogDetail {
  run_id: string;
  sha256: string;
  product_version: string;
  evidence_ids: string[];
  artifact: { observations: Record<string, unknown>[] };
  text_verified: boolean;
}
export type Preview =
  | { kind: "document"; citation: Citation }
  | { kind: "log"; event: Record<string, unknown> };
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
export function verifyPreview(hit: Hit, data: unknown): Preview {
  if (hit.kind === "log") {
    const detail = data as LogDetail;
    const event = detail.artifact.observations[hit.source.ordinal!];
    if (
      !detail.text_verified ||
      detail.sha256 !== hit.source.sha256 ||
      detail.run_id !== hit.source.run_id ||
      detail.product_version !== hit.product_version ||
      detail.evidence_ids[hit.source.ordinal!] !== hit.evidence_id ||
      canonical(event) !== canonical(hit.source.event)
    )
      throw new Error("观测回查与检索候选不一致。");
    return { kind: "log", event };
  }
  const citation = data as Citation;
  if (
    !citation.text_verified ||
    citation.snapshot.chunk_set_id !== hit.source.chunk_set_id ||
    citation.chunk.chunk_id !== hit.source.chunk_id ||
    citation.chunk.text !== hit.text ||
    citation.source.product_version !== hit.product_version ||
    citation.source.content_sha256 !== hit.source.content_sha256
  )
    throw new Error("原文回查与检索候选不一致。");
  return { kind: "document", citation };
}

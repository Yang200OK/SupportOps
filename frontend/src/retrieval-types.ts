export type Mode = "vector" | "bm25" | "rrf";
export type Kind = "document" | "case" | "log";
export interface Source {
  title?: string;
  filename?: string;
  document_id?: string;
  chunk_set_id?: string;
  chunk_id?: string;
  content_sha256?: string;
  heading_path?: string[];
  page_number?: number | null;
  json_pointer?: string | null;
  spans?: { line_start: number | null; line_end: number | null }[];
  experiment_id?: string;
  run_id?: string;
  ordinal?: number;
  sha256?: string;
  event?: Record<string, unknown>;
}
export interface Hit {
  rank: number;
  evidence_id: string;
  kind: Kind;
  product_version: string;
  text: string;
  source: Source;
  reference_url: string;
  cosine_similarity: number | null;
  bm25_score?: number | null;
  rrf_score?: number | null;
  vector_rank?: number | null;
  bm25_rank?: number | null;
  rerank_score?: number;
  retrieval_rank?: number;
  context_ids?: string[];
}
export interface Index {
  index_id: string;
  corpus_sha256: string;
  entry_count: number;
  created_at: string;
}
export interface Entry {
  product_version: string;
  kind: Kind;
  payload: { source: Source };
}
export interface Result {
  mode: Mode;
  eligible_count: number;
  items: Hit[];
  usage: { model_called: boolean; input_tokens: number | null; cost_cny: null };
  latency_ms: number;
  rerank_usage?: {
    model_called: boolean;
    input_tokens: number | null;
    requested_model?: string;
  };
  contexts?: Context[];
}

export interface Context {
  context_id: string;
  kind: "parent" | "child" | "log";
  source: Source & { parent_id?: string };
  start?: number;
  end?: number;
  text: string;
  text_sha256: string;
  anchor_evidence_ids: string[];
  text_verified: boolean;
  support_verified: boolean;
}

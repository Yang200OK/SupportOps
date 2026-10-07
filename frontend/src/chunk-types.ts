import type { Page } from "./types";

export interface ChunkSet {
  chunk_set_id: string;
  revision_id: string;
  revision_number: number;
  config: { max_chars: number; overlap_chars: number };
  chunker_version: string;
  content_sha256: string;
  parsed_sha256: string;
  snapshot_sha256: string;
  parent_count: number;
  chunk_count: number;
  created_at: string;
  reused: boolean;
}
export interface Chunk {
  chunk_id: string;
  parent_id: string;
  ordinal: number;
  kind: string;
  heading_path: string[];
  text: string;
  spans: {
    start: number;
    end: number;
    line_start: number | null;
    line_end: number | null;
  }[];
  page_number: number | null;
  json_pointer: string | null;
  table_headers: string[];
  code_language: string | null;
  atomic_oversize: boolean;
}
export interface ChunkPage extends Page<Chunk> {
  snapshot: ChunkSet;
}
export interface Citation {
  chunk: Chunk;
  snapshot: ChunkSet;
  parent: { heading_path: string[]; text: string; start: number; end: number };
  source: {
    title: string;
    filename: string;
    product_version: string;
    revision_number: number;
    content_sha256: string;
    position_basis: string;
  };
  parts: {
    start: number;
    end: number;
    line_start: number | null;
    line_end: number | null;
    before: string;
    excerpt: string;
    after: string;
  }[];
  text_verified: boolean;
  support_verified: boolean;
}

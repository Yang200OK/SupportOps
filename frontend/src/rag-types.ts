import type { Context, Result } from "./retrieval-types";

export interface Answer {
  answer_id: string;
  persisted: false;
  status: "no_evidence" | "insufficient_evidence" | "reviewed";
  product_version: string;
  retrieval: Result;
  contexts: Context[];
  claims: {
    claim_id: string;
    kind: "fact" | "hypothesis" | "check";
    text: string;
    support: {
      verdict: "supported" | "unsupported" | "insufficient";
      reason: string;
      human_reviewed: false;
    };
    citations: {
      context_id: string;
      evidence_id: string;
      quote: string;
      start: number;
      end: number;
      literal_verified: true;
      context_text_sha256: string;
    }[];
  }[];
  missing_information: string[];
  limitation: string;
  latency_ms: number;
  usage: {
    input_tokens: number;
    output_tokens: number;
    known_model_calls: number;
    unknown_usage_calls: number;
    cost_cny: null;
  };
}

import type { Answer } from "./rag-types";
import type { Result } from "./retrieval-types";

export interface Guided {
  original_query: string;
  rewritten_query: string | null;
  clarification: string | null;
  status:
    | "answered"
    | "limited_answer"
    | "needs_clarification"
    | "conflict"
    | "stopped"
    | "no_evidence"
    | "failed";
  stop_reason: string;
  questions: string[];
  conflicts: {
    topic: string;
    reason: string;
    citations: Answer["claims"][number]["citations"];
  }[];
  trace: {
    stage: string;
    query?: string;
    next_query?: string | null;
    reason?: string;
    action?: string;
    search?: number;
    new_evidence?: number;
    total_evidence?: number;
  }[];
  usage: Answer["usage"];
  answer: Answer | null;
  retrieval: Result | null;
}

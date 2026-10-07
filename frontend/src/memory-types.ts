export interface MemoryBody {
  title: string;
  symptoms: string;
  product_version: string;
  environment: string;
  mode: string;
  checks: { step_id: string; tool: string; reason: string }[];
  distinguishing_signals?: { support_signal: string; refute_signal: string }[];
  source_evidence: {
    evidence_id: string;
    reference_url: string;
    text_sha256: string;
  }[];
  limitation: string;
}
export interface Candidate {
  candidate_id: string;
  retrospective_id: string;
  body: MemoryBody;
  body_sha256: string;
  status: string;
  eligibility: string;
  revision: number;
  expires_at: string;
  events: unknown[];
  conflicts: { other_id: string; reason: string; blocking: boolean }[];
}
export interface Retrospective {
  retrospective_id: string;
  investigation_id: string;
  ticket_id: string;
  source_sha256: string;
  summary_sha256: string;
  summary: {
    status: string;
    event_count: number;
    plan_count: number;
    action: { status: string; retest_passed: boolean | null } | null;
    limitation: string;
    steps?: { step_id: string; tool: string; reason: string; status: string }[];
    hypotheses?: {
      hypothesis_id: string;
      cause: string;
      status: string;
      reason: string;
    }[];
    claims?: {
      claim_id: string;
      kind: string;
      text: string;
      support?: { verdict: string };
    }[];
    missing_information?: string[];
  };
  candidate: Candidate | null;
  source?: unknown;
  model_calls: number;
}
export interface MemoryBundle {
  loaded: {
    candidate_id: string;
    revision: number;
    body: MemoryBody;
    body_sha256: string;
    source_sha256: string;
  }[];
  selection_reason: string;
  selection_limited: boolean;
  limitation: string;
}

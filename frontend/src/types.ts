export interface Principal {
  username: string;
  organization_id: string;
  organization_name: string;
}
export interface TicketDraft {
  title: string;
  description: string;
  product: "relaydesk";
  product_version: "1.0" | "1.1" | "2.0" | null;
  environment: "local_lab";
  source_type: "synthetic_case" | "user_report";
}
export interface Ticket extends TicketDraft {
  ticket_id: string;
  created_at: string;
  intake_status: "ready_for_intake" | "needs_clarification";
  missing_fields: string[];
}
export interface Run {
  run_id: string;
  ticket_id: string;
  status: "succeeded" | "blocked";
  workflow_version: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  input_sha256: string;
  input_snapshot: Record<string, unknown>;
  output: { message: string; missing_fields: string[] };
  events: { sequence: number; event: string; elapsed_ms: number }[];
  model: null;
  usage: null;
  cost_cny: null;
}
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export interface EvaluationResult {
  dataset_id: string;
  total: number;
  dev: number;
  holdout: number;
  sha256: string;
  executed: false;
}

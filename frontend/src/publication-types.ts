export type Regression = {
  report_id: string;
  sha256: string;
  report: {
    passed: boolean;
    checks: { name: string; passed: boolean; error?: string }[];
    limitation?: string;
  };
};
export type SkillPublication = {
  draft_id: string;
  payload_sha256: string;
  revision: number;
  status: string;
  eligibility: string;
  payload: {
    candidate_id: string;
    candidate_revision: number;
    retrospective_id: string;
    source_sha256: string;
    method: {
      title: string;
      product_version: string;
      environment: string;
      mode: string;
      role: string;
      checks?: { step_id: string; tool: string; reason: string }[];
    };
  };
  reports: Regression[];
  decisions: unknown[];
  events: unknown[];
};

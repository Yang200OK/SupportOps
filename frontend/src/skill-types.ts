export interface SkillSummary {
  skill_id: string;
  version: string;
  title: string;
  summary: string;
  product_versions: string[];
  modes: string[];
  signals: string[];
  body_sha256: string;
  source_count: number;
}

export interface SkillDetail extends SkillSummary {
  body: string;
  catalog_sha256: string;
  body_verified: boolean;
  sources_verified: boolean;
  selected_product_version: string;
  selected_mode: string;
  sources: {
    path: string;
    sha256: string;
    source_type: string;
    license: string;
  }[];
  matched_signals?: string[];
}

export interface SkillCatalog {
  catalog_sha256: string;
  items: SkillSummary[];
}

export interface PublishedSkill {
  skill_id: string;
  version: string;
  title: string;
  body: string;
  body_sha256: string;
  matched_signals: string[];
  publication_id: string;
  publication_revision: number;
  payload_sha256: string;
  role: "planning_guidance_only";
  sources?: SkillDetail["sources"];
}

export interface SkillBundle {
  catalog: SkillCatalog;
  loaded: (SkillDetail | PublishedSkill)[];
  selection_reason: string;
  selection_limited: boolean;
}

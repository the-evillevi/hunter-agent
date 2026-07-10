-- Local development reset script.
-- Drops and recreates all tables. Do not run against data you want to keep.
PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DROP TABLE IF EXISTS resume_export_profiles;

DROP TABLE IF EXISTS resume_tailor_run_items;

DROP TABLE IF EXISTS resume_tailor_runs;

DROP TABLE IF EXISTS resume_items;

DROP TABLE IF EXISTS resume_sections;

DROP TABLE IF EXISTS resume_profiles;

DROP TABLE IF EXISTS blacklist;

DROP TABLE IF EXISTS applications;

DROP TABLE IF EXISTS profile_source_queries;

DROP TABLE IF EXISTS profile_location_types;

DROP TABLE IF EXISTS profile_keywords;

DROP TABLE IF EXISTS pipeline_runs;

DROP TABLE IF EXISTS score_layer_results;

DROP TABLE IF EXISTS score_runs;

DROP TABLE IF EXISTS jobs;

DROP TABLE IF EXISTS profiles;

DROP TABLE IF EXISTS keywords;

DROP TABLE IF EXISTS sources;

DROP TABLE IF EXISTS locations;

DROP TABLE IF EXISTS removed_sp500_companies;

DROP TABLE IF EXISTS companies;

CREATE TABLE companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  ticker TEXT UNIQUE,
  exchange TEXT,
  cik TEXT,
  sector TEXT,
  sub_industry TEXT,
  headquarters TEXT,
  date_added DATE,
  founded TEXT,
  sp500_source TEXT,
  sp500_source_url TEXT,
  is_sp500 BOOLEAN NOT NULL DEFAULT 0 CHECK (is_sp500 IN (0, 1)),
  sp500_weight_rank INTEGER CHECK (
    sp500_weight_rank IS NULL
    OR sp500_weight_rank >= 1
  ),
  sp500_tier TEXT CHECK (
    sp500_tier IS NULL
    OR sp500_tier IN (
      'mag7',
      'top100',
      'top200',
      'top300',
      'top400',
      'top500'
    )
  ),
  sp500_rank_source TEXT,
  sp500_rank_status TEXT CHECK (
    sp500_rank_status IS NULL
    OR sp500_rank_status IN (
      'weight_derived',
      'fallback_source_order',
      'unavailable'
    )
  ),
  sp500_provider TEXT,
  sp500_identifier TEXT,
  sp500_sedol TEXT,
  sp500_weight REAL,
  sp500_shares_held REAL,
  sp500_local_currency TEXT,
  sp500_holdings_as_of DATE,
  sp500_last_seen_at DATETIME,
  sp500_last_updated_at DATETIME
);

CREATE TABLE removed_sp500_companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER REFERENCES companies (id),
  ticker TEXT,
  name TEXT NOT NULL,
  removal_date DATE NOT NULL,
  removal_reason TEXT,
  source TEXT,
  source_url TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  enabled BOOLEAN NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  salary_min INTEGER NOT NULL DEFAULT 0 CHECK (salary_min >= 0),
  match_threshold INTEGER NOT NULL DEFAULT 80 CHECK (match_threshold BETWEEN 1 AND 100),
  active BOOLEAN NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE profile_keywords (
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  keyword_id INTEGER NOT NULL REFERENCES keywords (id),
  kind TEXT NOT NULL CHECK (kind IN ('include', 'exclude')),
  PRIMARY KEY (profile_id, keyword_id)
);

CREATE TABLE profile_location_types (
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  location_type TEXT NOT NULL CHECK (location_type IN ('remote', 'hybrid', 'onsite')),
  PRIMARY KEY (profile_id, location_type)
);

CREATE TABLE profile_source_queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  source_id INTEGER NOT NULL REFERENCES sources (id),
  query_json TEXT NOT NULL CHECK (json_valid(query_json)),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_profile_source_queries_profile_id
ON profile_source_queries (profile_id);

CREATE INDEX idx_profile_source_queries_source_id
ON profile_source_queries (source_id);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  title TEXT NOT NULL,
  company_id INTEGER NOT NULL REFERENCES companies (id),
  location_id INTEGER NOT NULL REFERENCES locations (id),
  url TEXT UNIQUE,
  source_id INTEGER NOT NULL REFERENCES sources (id),
  description TEXT,
  hash TEXT UNIQUE,
  scraped_at DATETIME NOT NULL,
  score INT CHECK (score BETWEEN 0 AND 100),
  score_reasoning TEXT
);

-- Append-only scoring history (HNTR-10). One score_runs row per pipeline
-- run of one job against one profile; jobs.score/score_reasoning stay a
-- latest-result cache for the UI, never the source of truth.
CREATE TABLE score_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs (id),
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  pipeline_version TEXT NOT NULL,
  weights_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('scored', 'rejected', 'failed')),
  score INT CHECK (score BETWEEN 0 AND 100),
  explanation TEXT,
  eligibility_reasons TEXT CHECK (
    eligibility_reasons IS NULL OR json_valid(eligibility_reasons)
  ), -- JSON array of {code, detail}
  unknowns TEXT CHECK (unknowns IS NULL OR json_valid(unknowns)),
  warnings TEXT CHECK (warnings IS NULL OR json_valid(warnings)),
  duration_ms INT CHECK (duration_ms >= 0),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_score_runs_job_profile ON score_runs (job_id, profile_id, created_at);

-- One row per registered layer per run: success rows carry scores and
-- versions, skip/failure rows carry bounded failure diagnostics.
CREATE TABLE score_layer_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  score_run_id INTEGER NOT NULL REFERENCES score_runs (id),
  layer TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'skip', 'failure')),
  algorithm_version TEXT,
  model TEXT,
  prompt_version TEXT,
  score INT CHECK (score BETWEEN 0 AND 100),
  explanation TEXT,
  duration_ms INT CHECK (duration_ms >= 0),
  details TEXT CHECK (details IS NULL OR json_valid(details)),
  failure_code TEXT,
  failure_detail TEXT CHECK (
    failure_detail IS NULL OR length(failure_detail) <= 500
  ),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_score_layer_results_run ON score_layer_results (score_run_id);

-- Append-only pipeline audit (HNTR-51). Exactly one row per pipeline run —
-- manual or scheduled — with headline counts and bounded error text.
-- Per-job scoring detail already lives in score_runs; stage diagnostics
-- belong in logs.
CREATE TABLE pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual', 'scheduled')),
  status TEXT NOT NULL CHECK (
    status IN ('success', 'partial', 'failure', 'skipped_overlap')
  ),
  started_at DATETIME NOT NULL,
  finished_at DATETIME,
  discovered_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
  persisted_count INTEGER NOT NULL DEFAULT 0 CHECK (persisted_count >= 0),
  rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
  scored_count INTEGER NOT NULL DEFAULT 0 CHECK (scored_count >= 0),
  failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
  errors TEXT CHECK (errors IS NULL OR json_valid(errors)), -- JSON array of {stage, message}
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pipeline_runs_started_at ON pipeline_runs (started_at);

CREATE TABLE applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL UNIQUE REFERENCES jobs (id),
  cv_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (
    status IN (
      'pending',
      'draft',
      'applied',
      'acknowledged',
      'interviews',
      'rejected',
      'ghosted',
      'offer',
      'accepted'
    )
  ),
  applied_at DATETIME,
  last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes TEXT
);

CREATE INDEX idx_applications_last_updated ON applications (last_updated);

CREATE INDEX idx_applications_status ON applications (status);

CREATE TABLE blacklist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER REFERENCES companies (id),
  job_id INTEGER REFERENCES jobs (id),
  reason TEXT,
  added_at DATETIME NOT NULL,
  CHECK (
    (
      company_id IS NOT NULL
      AND job_id IS NULL
    )
    OR (
      company_id IS NULL
      AND job_id IS NOT NULL
    )
  )
);

-- Resume storage: structured CV facts instead of opaque file paths.
-- A profile is one resume version; base_resume_id points at the master
-- for tailored variants, and job_id marks which job a variant targets.
-- deleted_at implements soft deletes: application code hides profiles
-- with a timestamp instead of destroying their audit trail. Deleting a
-- base resume must not destroy its variants, so the self-reference
-- nulls out instead of cascading.
CREATE TABLE resume_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  base_resume_id INTEGER REFERENCES resume_profiles (id) ON DELETE SET NULL,
  job_id INTEGER REFERENCES jobs (id),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME
);

CREATE TABLE resume_sections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES resume_profiles (id) ON DELETE CASCADE,
  section_type TEXT NOT NULL CHECK (
    section_type IN (
      'basics',
      'summary',
      'experience',
      'education',
      'skills',
      'projects',
      'certifications'
    )
  ),
  title TEXT NOT NULL,
  order_idx INTEGER NOT NULL DEFAULT 0
);

-- content holds the JSON-encoded fact payload (company, dates, bullets, ...)
-- so new fact shapes never require a schema migration. relevance_score and
-- score_reasoning stay NULL until a tailoring run scores the item.
CREATE TABLE resume_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_id INTEGER NOT NULL REFERENCES resume_sections (id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  relevance_score REAL CHECK (
    relevance_score IS NULL
    OR relevance_score BETWEEN 0 AND 100
  ),
  score_reasoning TEXT,
  score_is_fallback BOOLEAN NOT NULL DEFAULT 0 CHECK (score_is_fallback IN (0, 1)),
  order_idx INTEGER NOT NULL DEFAULT 0
);

-- Audit log: one row per tailoring run that produced a variant for a job.
-- duration_ms records the wall-clock time of the whole run (scoring plus
-- variant writes).
CREATE TABLE resume_tailor_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_profile_id INTEGER NOT NULL REFERENCES resume_profiles (id) ON DELETE CASCADE,
  output_profile_id INTEGER NOT NULL REFERENCES resume_profiles (id) ON DELETE CASCADE,
  job_id INTEGER NOT NULL REFERENCES jobs (id),
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Audit detail: one scored fact per run, whether kept or dropped. Items
-- below the relevance threshold never reach the variant, so this table is
-- the only place their scores and reasoning survive for analytics.
-- item_content is a JSON snapshot because base items can be edited or
-- deleted after the run.
CREATE TABLE resume_tailor_run_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES resume_tailor_runs (id) ON DELETE CASCADE,
  section_type TEXT NOT NULL,
  item_content TEXT NOT NULL,
  score REAL CHECK (
    score IS NULL
    OR score BETWEEN 0 AND 100
  ),
  reasoning TEXT,
  is_fallback BOOLEAN NOT NULL DEFAULT 0 CHECK (is_fallback IN (0, 1)),
  kept BOOLEAN NOT NULL CHECK (kept IN (0, 1))
);

CREATE INDEX idx_resume_tailor_run_items_run ON resume_tailor_run_items (run_id);

-- Saved export configurations: which format and sections to compile.
-- section_filters holds a JSON-encoded list of section type names, or
-- NULL to export every section.
CREATE TABLE resume_export_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  format TEXT NOT NULL CHECK (
    format IN ('json', 'json_resume', 'html', 'pdf')
  ),
  include_scores BOOLEAN NOT NULL DEFAULT 0 CHECK (include_scores IN (0, 1)),
  section_filters TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_resume_sections_profile ON resume_sections (profile_id);

CREATE INDEX idx_resume_items_section ON resume_items (section_id);

COMMIT;

-- Local development reset script.
-- Drops and recreates all tables. Do not run against data you want to keep.
PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DROP TABLE IF EXISTS blacklist;

DROP TABLE IF EXISTS applications;

DROP TABLE IF EXISTS profile_keywords;

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
    OR sp500_weight_rank BETWEEN 1 AND 500
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
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role_name TEXT,
  salary_min INT,
  location_type TEXT CHECK (location_type IN ('remote', 'hybrid', 'onsite')),
  match_threshold INT CHECK (match_threshold BETWEEN 1 AND 100),
  active BOOLEAN NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE profile_keywords (
  profile_id INTEGER NOT NULL REFERENCES profiles (id),
  keyword_id INTEGER NOT NULL REFERENCES keywords (id),
  PRIMARY KEY (profile_id, keyword_id)
);

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
  score INT CHECK (score BETWEEN 1 AND 100),
  score_reasoning TEXT
);

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

COMMIT;

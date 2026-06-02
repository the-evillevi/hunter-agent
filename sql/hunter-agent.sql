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

DROP TABLE IF EXISTS companies;

CREATE TABLE companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
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

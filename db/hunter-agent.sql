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
	location_type TEXT CHECK(location_type IN ('remote', 'hybrid', 'onsite')),
	match_threshold INT CHECK(match_threshold BETWEEN 1 AND 100),
	active BOOLEAN
);

CREATE TABLE profile_keywords (
	profile_id INTEGER NOT NULL REFERENCES profiles(id),
	keyword_id INTEGER NOT NULL REFERENCES keywords(id),
	PRIMARY KEY (profile_id, keyword_id)
);

CREATE TABLE jobs (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	profile_id INTEGER NOT NULL REFERENCES profiles(id),
	title TEXT,
	company_id INTEGER NOT NULL REFERENCES companies(id),
	location_id INTEGER NOT NULL REFERENCES locations(id),
	url TEXT UNIQUE,
	source_id INTEGER NOT NULL REFERENCES sources(id),
	description TEXT,
	hash TEXT UNIQUE,
	scraped_at DATE,
	score INT CHECK(score BETWEEN 1 AND 100),
	score_reasoning TEXT
);

CREATE TABLE applications (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	job_id INT,
	cv_path TEXT,
	status TEXT CHECK(status IN ('pending', 'draft', 'applied', 'acknowledged', 'interviews', 'rejected', 'ghosted', 'offer', 'accepted')),
	applied_at DATE,
	last_updated DATE,
	notes TEXT,

	FOREIGN KEY (job_id) REFERENCES jobs (id)
);

CREATE TABLE blacklist (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	company_id INTEGER REFERENCES companies(id),
	job_id INTEGER REFERENCES jobs(id),
	reason TEXT,
	added_at DATE,
	CHECK (
		(company_id IS NOT NULL AND job_id IS NULL)
		OR
		(company_id IS NULL AND job_id IS NOT NULL)
	)
);


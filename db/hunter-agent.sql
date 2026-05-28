CREATE TABLE profiles (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	role_name TEXT,
	keywords TEXT, -- shpuld be an array of text, or maybe a relation?
	salary_min TEXT,
	location_type TEXT CHECK(location_type IN ('remote', 'hybrid', 'onsite')),
	match_threshold INT CHECK(match_threshold BETWEEN 1 AND 100),
	active BOOLEAN
);

CREATE TABLE jobs (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	profile_id INT,
	title TEXT,
	company TEXT, -- should be a relation?
	location TEXT, -- should be a relation?
	url TEXT,
	source TEXT, -- should be a relation?
	description TEXT,
	hash TEXT, -- what's the type for hash?
	scraped_at DATE,
	score INT, -- between 1 and 100
	score_reasoning TEXT,

	FOREIGN KEY (profile_id) REFERENCES profiles (id)
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
	job_id INT,
	company_name TEXT, -- same as jobs.company
	reason TEXT,
	added_at DATE,

	FOREIGN KEY (job_id) REFERENCES jobs (id)
);


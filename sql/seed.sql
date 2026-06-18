-- Sample data for hunter-agent
-- Generated from config.toml profiles: AI Engineer, Senior Fullstack Engineer
-- Locations focused on Mexican tech hubs: CDMX, GDL, QRO
-- Local development seed script. Do not run against data you want to keep.
PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- Companies
INSERT INTO companies (name) VALUES ('Palantir Technologies');
INSERT INTO companies (name) VALUES ('Kavak');
INSERT INTO companies (name) VALUES ('Globant');

-- Locations
INSERT INTO locations (name) VALUES ('CDMX');
INSERT INTO locations (name) VALUES ('Guadalajara, GDL');
INSERT INTO locations (name) VALUES ('Querétaro, QRO');

-- Sources
INSERT INTO sources (name, enabled) VALUES ('Adzuna', 1);
INSERT INTO sources (name, enabled) VALUES ('Remotive', 1);

-- Keywords
INSERT INTO keywords (name) VALUES ('Python');
INSERT INTO keywords (name) VALUES ('PyTorch');
INSERT INTO keywords (name) VALUES ('LLM');
INSERT INTO keywords (name) VALUES ('React');
INSERT INTO keywords (name) VALUES ('TypeScript');

-- Profiles (matching config.toml definitions)
INSERT INTO profiles (role_name, salary_min, location_type, match_threshold, active)
VALUES ('AI Engineer', 60000, 'remote', 80, 1);

INSERT INTO profiles (role_name, salary_min, location_type, match_threshold, active)
VALUES ('Senior Fullstack Engineer', 60000, 'hybrid', 80, 1);

-- Profile-Keyword mappings
INSERT INTO profile_keywords (profile_id, keyword_id) VALUES (1, 1); -- AI Engineer -> Python
INSERT INTO profile_keywords (profile_id, keyword_id) VALUES (1, 2); -- AI Engineer -> PyTorch
INSERT INTO profile_keywords (profile_id, keyword_id) VALUES (1, 3); -- AI Engineer -> LLM
INSERT INTO profile_keywords (profile_id, keyword_id) VALUES (2, 4); -- Sr Fullstack -> React
INSERT INTO profile_keywords (profile_id, keyword_id) VALUES (2, 5); -- Sr Fullstack -> TypeScript

-- Jobs
INSERT INTO jobs (profile_id, title, company_id, location_id, url, source_id, description, hash, scraped_at, score, score_reasoning)
VALUES (
    1,
    'AI/ML Engineer',
    2,
    1,
    'https://www.adzuna.com/jobs/view/kavak-ai-ml-engineer-001',
    1,
    'Kavak is looking for an AI/ML Engineer to join our team in CDMX. You will work on developing and deploying machine learning models at scale, with a focus on LLMs and recommendation systems. Proficiency in Python and PyTorch is required. Experience with RAG pipelines and fine-tuning is a plus.',
    'kavak-aiml-hash-001',
    datetime('now', '-3 days'),
    88,
    'Strong match for AI Engineer profile (score: 88/100). All core keywords found: Python, PyTorch, LLM. Relevant experience with ML models at scale. Salary range likely aligns with 60k+ MXN.'
);

INSERT INTO jobs (profile_id, title, company_id, location_id, url, source_id, description, hash, scraped_at, score, score_reasoning)
VALUES (
    2,
    'Senior Fullstack Engineer',
    3,
    2,
    'https://remotive.com/remote-jobs/globant-senior-fullstack-002',
    2,
    'Globant is seeking a Senior Fullstack Engineer to work from our Guadalajara office in a hybrid model. You will build modern web applications using React, TypeScript, and Node.js. Experience with PostgreSQL, REST API design, and Next.js is highly valued. You will collaborate with cross-functional teams across Latin America.',
    'globant-fullstack-hash-002',
    datetime('now', '-1 day'),
    82,
    'Good match for Senior Fullstack Engineer profile (score: 82/100). Keywords found: React, TypeScript, REST API, PostgreSQL. Hybrid model in GDL aligns with location preference.'
);

-- Applications
INSERT INTO applications (job_id, cv_path, status, applied_at, last_updated, notes)
VALUES (
    1,
    '/Users/evillevi/cv/master.docx',
    'pending',
    NULL,
    datetime('now'),
    'Tailor CV to highlight PyTorch and LLM experience. Prepare brief note about previous work with recommendation systems.'
);

-- Blacklist
INSERT INTO blacklist (company_id, reason, added_at)
VALUES (
    1,
    'Defense contractor — ethical concerns regarding military and surveillance contracts. Not aligned with personal values.',
    datetime('now')
);

COMMIT;

PRAGMA foreign_keys = ON;

-- table: classlist
CREATE TABLE classlist (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	names_json VARCHAR NOT NULL,
	source_json VARCHAR,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

-- table: exam
CREATE TABLE exam (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	teacher_style_profile_json VARCHAR,
	front_page_template_json VARCHAR,
	class_list_json VARCHAR,
	class_list_source_json VARCHAR,
	status VARCHAR(15) NOT NULL,
	PRIMARY KEY (id)
);

-- table: exambulkuploadfile
CREATE TABLE exambulkuploadfile (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	original_filename VARCHAR NOT NULL,
	stored_path VARCHAR NOT NULL,
	source_manifest_json VARCHAR,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: examkeyfile
CREATE TABLE examkeyfile (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	original_filename VARCHAR NOT NULL,
	stored_path VARCHAR NOT NULL,
	blob_url VARCHAR,
	blob_pathname VARCHAR,
	content_type VARCHAR NOT NULL,
	size_bytes INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: examkeypage
CREATE TABLE examkeypage (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	image_path VARCHAR NOT NULL,
	blob_pathname VARCHAR,
	blob_url VARCHAR,
	width INTEGER NOT NULL,
	height INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: examkeyparsejob
CREATE TABLE examkeyparsejob (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	page_count INTEGER NOT NULL,
	pages_done INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	cost_total FLOAT NOT NULL,
	input_tokens_total INTEGER NOT NULL,
	output_tokens_total INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: question
CREATE TABLE question (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	label VARCHAR NOT NULL,
	max_marks INTEGER NOT NULL,
	rubric_json VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: submission
CREATE TABLE submission (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	student_name VARCHAR NOT NULL,
	first_name VARCHAR NOT NULL,
	last_name VARCHAR NOT NULL,
	status VARCHAR(11) NOT NULL,
	capture_mode VARCHAR(17) NOT NULL,
	front_page_totals_json VARCHAR,
	front_page_candidates_json VARCHAR,
	front_page_usage_json VARCHAR,
	front_page_reviewed_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: answercrop
CREATE TABLE answercrop (
	id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	image_path VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES submission (id),
	FOREIGN KEY(question_id) REFERENCES question (id)
);

-- table: bulkuploadpage
CREATE TABLE bulkuploadpage (
	id INTEGER NOT NULL,
	bulk_upload_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	image_path VARCHAR NOT NULL,
	width INTEGER NOT NULL,
	height INTEGER NOT NULL,
	detected_student_name VARCHAR,
	detection_confidence FLOAT NOT NULL,
	detection_evidence_json VARCHAR,
	front_page_usage_json VARCHAR,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(bulk_upload_id) REFERENCES exambulkuploadfile (id)
);

-- table: examintakejob
CREATE TABLE examintakejob (
	id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	bulk_upload_id INTEGER,
	status VARCHAR NOT NULL,
	stage VARCHAR NOT NULL,
	page_count INTEGER NOT NULL,
	pages_built INTEGER NOT NULL,
	pages_processed INTEGER NOT NULL,
	submissions_created INTEGER NOT NULL,
	candidates_ready INTEGER NOT NULL,
	review_open_threshold INTEGER NOT NULL,
	initial_review_ready BOOLEAN NOT NULL,
	fully_warmed BOOLEAN NOT NULL,
	review_ready BOOLEAN NOT NULL,
	thinking_level VARCHAR NOT NULL,
	attempt_count INTEGER NOT NULL,
	runner_id VARCHAR,
	lease_expires_at DATETIME,
	started_at DATETIME,
	finished_at DATETIME,
	last_progress_at DATETIME,
	metrics_json VARCHAR,
	error_message VARCHAR,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id),
	FOREIGN KEY(bulk_upload_id) REFERENCES exambulkuploadfile (id)
);

-- table: examkeyparsepage
CREATE TABLE examkeyparsepage (
	id INTEGER NOT NULL,
	job_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	confidence FLOAT NOT NULL,
	model_used VARCHAR,
	result_json JSON,
	error_json JSON,
	cost FLOAT NOT NULL,
	input_tokens INTEGER NOT NULL,
	output_tokens INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES examkeyparsejob (id)
);

-- table: graderesult
CREATE TABLE graderesult (
	id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	marks_awarded FLOAT NOT NULL,
	breakdown_json VARCHAR NOT NULL,
	feedback_json VARCHAR NOT NULL,
	model_name VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES submission (id),
	FOREIGN KEY(question_id) REFERENCES question (id)
);

-- table: questionparseevidence
CREATE TABLE questionparseevidence (
	id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	exam_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	x FLOAT NOT NULL,
	y FLOAT NOT NULL,
	w FLOAT NOT NULL,
	h FLOAT NOT NULL,
	evidence_kind VARCHAR NOT NULL,
	confidence FLOAT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(question_id) REFERENCES question (id),
	FOREIGN KEY(exam_id) REFERENCES exam (id)
);

-- table: questionregion
CREATE TABLE questionregion (
	id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	x FLOAT NOT NULL,
	y FLOAT NOT NULL,
	w FLOAT NOT NULL,
	h FLOAT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(question_id) REFERENCES question (id)
);

-- table: submissionfile
CREATE TABLE submissionfile (
	id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	file_kind VARCHAR NOT NULL,
	original_filename VARCHAR NOT NULL,
	stored_path VARCHAR NOT NULL,
	blob_url VARCHAR,
	blob_pathname VARCHAR,
	content_type VARCHAR NOT NULL,
	size_bytes INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES submission (id)
);

-- table: submissionpage
CREATE TABLE submissionpage (
	id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	page_number INTEGER NOT NULL,
	image_path VARCHAR NOT NULL,
	width INTEGER NOT NULL,
	height INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES submission (id)
);

-- table: transcription
CREATE TABLE transcription (
	id INTEGER NOT NULL,
	submission_id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	provider VARCHAR NOT NULL,
	text VARCHAR NOT NULL,
	confidence FLOAT NOT NULL,
	raw_json VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES submission (id),
	FOREIGN KEY(question_id) REFERENCES question (id)
);

CREATE INDEX ix_answercrop_question_id ON answercrop (question_id);
CREATE INDEX ix_answercrop_submission_id ON answercrop (submission_id);
CREATE INDEX ix_bulkuploadpage_bulk_upload_id ON bulkuploadpage (bulk_upload_id);
CREATE INDEX ix_exambulkuploadfile_exam_id ON exambulkuploadfile (exam_id);
CREATE INDEX ix_examintakejob_bulk_upload_id ON examintakejob (bulk_upload_id);
CREATE INDEX ix_examintakejob_exam_id ON examintakejob (exam_id);
CREATE INDEX ix_examkeyfile_exam_id ON examkeyfile (exam_id);
CREATE INDEX ix_examkeypage_exam_id ON examkeypage (exam_id);
CREATE INDEX ix_examkeyparsejob_exam_id ON examkeyparsejob (exam_id);
CREATE INDEX ix_examkeyparsepage_job_id ON examkeyparsepage (job_id);
CREATE INDEX ix_graderesult_question_id ON graderesult (question_id);
CREATE INDEX ix_graderesult_submission_id ON graderesult (submission_id);
CREATE INDEX ix_question_exam_id ON question (exam_id);
CREATE INDEX ix_questionparseevidence_exam_id ON questionparseevidence (exam_id);
CREATE INDEX ix_questionparseevidence_question_id ON questionparseevidence (question_id);
CREATE INDEX ix_questionregion_question_id ON questionregion (question_id);
CREATE INDEX ix_submission_exam_id ON submission (exam_id);
CREATE INDEX ix_submissionfile_submission_id ON submissionfile (submission_id);
CREATE INDEX ix_submissionpage_submission_id ON submissionpage (submission_id);
CREATE INDEX ix_transcription_question_id ON transcription (question_id);
CREATE INDEX ix_transcription_submission_id ON transcription (submission_id);

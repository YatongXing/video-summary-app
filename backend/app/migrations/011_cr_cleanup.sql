-- Four-state cleanup controls; no physical deletion or permission cutover.
DROP INDEX cleanup_jobs_due_idx;
ALTER TABLE cleanup_jobs DROP CONSTRAINT cleanup_jobs_status_check;
ALTER TABLE cleanup_jobs RENAME COLUMN last_attempt_at TO last_physical_started_at;
ALTER TABLE cleanup_jobs ADD COLUMN job_version bigint NOT NULL DEFAULT 0 CHECK(job_version>=0);
ALTER TABLE cleanup_jobs ADD COLUMN consecutive_failures integer NOT NULL DEFAULT 0 CHECK(consecutive_failures>=0);
-- Unknown legacy streaks must not silently reset known failed work.
UPDATE cleanup_jobs SET consecutive_failures=attempt_count;
ALTER TABLE cleanup_jobs ADD COLUMN online_completed_at timestamptz;
ALTER TABLE cleanup_jobs ADD COLUMN blocked_reason text;
UPDATE cleanup_jobs SET next_attempt_at=CASE WHEN status='MANUAL_REQUIRED' AND last_error_code IN('ASSET_CLEANUP_FAILED','OBJECT_STILL_PRESENT') THEN GREATEST(next_attempt_at,last_physical_started_at+interval '60 seconds',clock_timestamp()) ELSE next_attempt_at END;
UPDATE cleanup_jobs SET blocked_reason=CASE status WHEN 'RUNNING' THEN 'PRODUCER_UNKNOWN' WHEN 'MANUAL_REQUIRED' THEN CASE WHEN last_error_code IS NULL OR last_error_code IN('ASSET_CLEANUP_FAILED','OBJECT_STILL_PRESENT') THEN NULL ELSE 'UNSAFE' END END,
 status=CASE status WHEN 'RUNNING' THEN 'BLOCKED' WHEN 'MANUAL_REQUIRED' THEN CASE WHEN last_error_code IS NULL THEN 'PENDING' WHEN last_error_code IN('ASSET_CLEANUP_FAILED','OBJECT_STILL_PRESENT') THEN 'RETRY_WAIT' ELSE 'BLOCKED' END WHEN 'COMPLETED' THEN 'PENDING' ELSE status END,
 completed_at=NULL;
UPDATE media_assets a SET cleanup_state='REGISTERED',deleted_at=NULL
 WHERE EXISTS(SELECT 1 FROM cleanup_jobs j WHERE j.task_id=a.task_id);
ALTER TABLE cleanup_jobs DROP COLUMN worker_safe_after, DROP COLUMN manual_requested,
 DROP COLUMN backup_cleanup_state, DROP COLUMN backup_completed_at;
ALTER TABLE cleanup_jobs ADD CONSTRAINT cleanup_four_states CHECK(status IN('PENDING','RETRY_WAIT','BLOCKED','COMPLETED'));
ALTER TABLE cleanup_jobs ADD CONSTRAINT cleanup_blocked CHECK(
 (status='BLOCKED' AND blocked_reason IS NOT NULL AND blocked_reason IN('CONFIG','UNSAFE','LEGACY_APPROVAL','PRODUCER_UNKNOWN'))
 OR (status<>'BLOCKED' AND blocked_reason IS NULL));
ALTER TABLE cleanup_jobs ADD CONSTRAINT cleanup_completion CHECK(
 (status='COMPLETED' AND completed_at IS NOT NULL AND online_completed_at IS NOT NULL)
 OR (status<>'COMPLETED' AND completed_at IS NULL));
CREATE INDEX cleanup_jobs_due_idx ON cleanup_jobs(next_attempt_at) WHERE status IN('PENDING','RETRY_WAIT');
ALTER TABLE media_assets ADD COLUMN storage_backend text;
ALTER TABLE media_assets ADD COLUMN storage_bucket text;
ALTER TABLE media_assets ADD COLUMN storage_version text;
ALTER TABLE media_assets ADD COLUMN cleanup_approved_at timestamptz;
UPDATE media_assets SET storage_backend=CASE WHEN asset_kind IN('RESULT_JSON','RESULT_DOCUMENT') THEN 'DB' WHEN asset_kind='PROCESSING_WORKSPACE' THEN 'WORKSPACE' ELSE 'S3' END;
ALTER TABLE media_assets ALTER COLUMN storage_backend SET NOT NULL;
ALTER TABLE media_assets ADD CONSTRAINT asset_backend CHECK(storage_backend IN('DB','WORKSPACE','S3') OR storage_backend ~ '^(S3|WORKSPACE):[0-9a-f]{64}$');
ALTER TABLE media_assets ADD CONSTRAINT asset_location CHECK((storage_backend='S3' OR storage_backend LIKE 'S3:%') OR(storage_bucket IS NULL AND storage_version IS NULL));
ALTER TABLE media_assets ADD CONSTRAINT asset_approval CHECK(cleanup_approved_at IS NULL OR asset_kind='BACKUP');
CREATE FUNCTION guard_cleanup_asset() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_name name;
BEGIN
 IF TG_OP='INSERT' THEN
  NEW.storage_backend=COALESCE(NEW.storage_backend,CASE WHEN NEW.asset_kind IN('RESULT_JSON','RESULT_DOCUMENT') THEN 'DB' WHEN NEW.asset_kind='PROCESSING_WORKSPACE' THEN 'WORKSPACE' ELSE 'S3' END);
 ELSE
  IF (NEW.id,NEW.task_id,NEW.user_id,NEW.asset_kind,NEW.storage_key,NEW.storage_backend,NEW.storage_bucket,NEW.storage_version)
   IS DISTINCT FROM(OLD.id,OLD.task_id,OLD.user_id,OLD.asset_kind,OLD.storage_key,OLD.storage_backend,OLD.storage_bucket,OLD.storage_version) THEN
   RAISE EXCEPTION 'Immutable asset identity' USING ERRCODE='23514';
  END IF;
 END IF;
 IF (TG_OP='INSERT' AND NEW.cleanup_approved_at IS NOT NULL) OR (TG_OP='UPDATE' AND NEW.cleanup_approved_at IS DISTINCT FROM OLD.cleanup_approved_at) THEN
  SELECT pg_get_userbyid(relowner) INTO owner_name FROM pg_class WHERE oid=TG_RELID;
  IF current_user<>owner_name THEN RAISE EXCEPTION 'Maintenance owner required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cleanup_asset_guard BEFORE INSERT OR UPDATE ON media_assets FOR EACH ROW EXECUTE FUNCTION guard_cleanup_asset();
CREATE FUNCTION guard_cleanup_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.job_version<>OLD.job_version+1 THEN RAISE EXCEPTION 'Cleanup version must advance' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cleanup_version_guard BEFORE UPDATE ON cleanup_jobs FOR EACH ROW EXECUTE FUNCTION guard_cleanup_version();

ALTER TABLE input_imports ADD COLUMN storage_backend text;
ALTER TABLE input_imports ADD COLUMN storage_bucket text;
ALTER TABLE input_imports ADD COLUMN storage_version text;
ALTER TABLE input_imports ADD COLUMN staging_backend text;
CREATE FUNCTION guard_import_location() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.id,NEW.user_id,NEW.object_key,NEW.storage_backend,NEW.storage_bucket,NEW.storage_version,NEW.staging_backend)
 IS DISTINCT FROM(OLD.id,OLD.user_id,OLD.object_key,OLD.storage_backend,OLD.storage_bucket,OLD.storage_version,OLD.staging_backend)
 THEN RAISE EXCEPTION 'Immutable import location' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER import_location_guard BEFORE UPDATE ON input_imports FOR EACH ROW EXECUTE FUNCTION guard_import_location();

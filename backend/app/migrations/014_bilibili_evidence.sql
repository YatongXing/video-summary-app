-- This pilot migration keeps existing 013 inputs as full media.
ALTER TABLE tasks
    ADD COLUMN bilibili_evidence text NOT NULL DEFAULT 'FULL_MEDIA',
    ADD COLUMN bilibili_transcript jsonb,
    ADD CONSTRAINT tasks_bilibili_evidence_check CHECK (
        bilibili_evidence IN ('FULL_MEDIA','PLATFORM_SUBTITLES','AUDIO_ONLY')
        AND (input_kind = 'BILIBILI' OR bilibili_evidence = 'FULL_MEDIA')
        AND (bilibili_transcript IS NULL OR bilibili_evidence = 'AUDIO_ONLY')
    );

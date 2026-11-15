ALTER TABLE tasks ADD COLUMN requested_model text NOT NULL DEFAULT 'qwen3.8-omni-flash'
    CHECK (requested_model IN ('qwen3.8-omni-flash','deepseek-v4.1-flash'));

ALTER TABLE model_calls
    ADD COLUMN deepseek_phase text CHECK (deepseek_phase IN (
        'ASR_SUBMITTING','ASR_WAITING','ASR_COMPLETE','SUMMARY_SUBMITTING','FINISHED'
    )),
    ADD COLUMN asr_provider_task_id text;

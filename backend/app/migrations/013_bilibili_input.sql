-- Refuse to silently convert or delete an unexpected historical direct URL task.
SET LOCAL lock_timeout = '5s';
ALTER TABLE tasks DROP CONSTRAINT tasks_input_kind_check,
    ADD CONSTRAINT tasks_input_kind_check CHECK (input_kind IN ('LOCAL', 'BILIBILI'));

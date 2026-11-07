"""Historical support rows: deletion compatibility only, never an access grant."""


def close_for_task(c, task_id, actor_id=None):
    rows=c.execute("""UPDATE support_requests SET closed_at=clock_timestamp(),close_reason='TASK_DELETED'
        WHERE task_id=%s AND closed_at IS NULL RETURNING user_id,task_id""",(task_id,)).fetchall()
    if rows:
        c.execute('UPDATE tasks SET support_version=support_version+1 WHERE id=%s',(task_id,))
        for row in rows:
            c.execute("""INSERT INTO audit_events(actor_user_id,target_user_id,task_id,event_type,
                reason_code,result,retention_class,created_at)
                VALUES(%s,%s,%s,'SUPPORT_TASK_DELETED','SUPPORT_TASK_DELETED','ALLOWED','DELETION_90D',clock_timestamp())""",
                (actor_id,row['user_id'],row['task_id']))

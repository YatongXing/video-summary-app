"""Two synthetic DB roles: no production privilege changes or M3."""
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from cr_wp02_fixtures import wp02_factory, seed, request
import pytest


def test_two_role_boundary_legacy_cleanup_and_maintenance_reconnect(wp02_factory):
    from app.migrate import run_migrations
    db=wp02_factory(migrate=False)
    maintain="wp02_maint_"+uuid4().hex
    runtime="wp02_run_"+uuid4().hex
    password="synthetic-wp02-role-only"
    roles=[maintain,runtime]
    with db.connect() as admin:
        try:
            for role in roles:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role),sql.Literal(password)))
            admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(db.schema),sql.Identifier(maintain)))
            with db.connect(user=maintain,password=password) as owner:
                run_migrations(owner)
                functions = owner.execute(
                    "SELECT proname,prosecdef FROM pg_proc WHERE pronamespace=current_schema()::regnamespace "
                    "AND proname IN ('guard_support_request_update','guard_task_support_version_update')"
                ).fetchall()
                assert sorted(functions) == [
                    ('guard_support_request_update',False),
                    ('guard_task_support_version_update',False),
                ]
                u,t=seed(owner);r=request(owner,u,t)
                owner.execute("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES(%s,%s,'synthetic','summary')",(t,u))
                owner.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(db.schema),sql.Identifier(runtime)))
                owner.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON tasks,support_requests TO {}").format(sql.Identifier(runtime)))
                owner.execute(sql.SQL("GRANT SELECT(task_id,user_id),DELETE ON task_consents TO {}").format(sql.Identifier(runtime)))
            with db.connect(user=maintain,password=password) as owner:
                assert owner.execute("SELECT current_user").fetchone()==(maintain,)
            with db.connect(user=runtime,password=password) as c:
                flags=c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
                assert flags==(False,False,False)
                for command in ["CREATE TABLE forbidden_probe(id int)","CREATE TABLE public.forbidden_probe(id int)",
                                "CREATE ROLE forbidden_role",f"SET ROLE {maintain}",
                                "SELECT purpose,access_scope,revoked_at FROM task_consents",
                                "INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES(NULL,NULL,'x','summary')",
                                "UPDATE task_consents SET revoked_at=now()"]:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        with c.transaction():c.execute(command)
                # Version triggers still execute correctly as the non-owner.
                c.execute("UPDATE tasks SET support_version=support_version+1 WHERE id=%s",(t,))
                with pytest.raises(psycopg.IntegrityError):
                    with c.transaction():c.execute("UPDATE tasks SET support_version=0 WHERE id=%s",(t,))
                assert c.execute("SELECT count(*) FROM support_requests").fetchone()==(1,)
                # Minimal legacy DELETE and scope columns permit actual cleanup.
                c.execute("DELETE FROM task_consents WHERE task_id=%s AND user_id=%s",(t,u))
                c.execute("DELETE FROM support_requests WHERE id=%s",(r,))
                c.execute("DELETE FROM tasks WHERE id=%s",(t,))
        finally:
            admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO CURRENT_USER").format(sql.Identifier(db.schema)))
            for role in roles:
                # Exact UUID roles only, in this disposable instance.
                admin.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

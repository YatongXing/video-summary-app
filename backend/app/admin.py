"""Deployer-only administration; password creation requires a real terminal."""
import argparse
from getpass import getpass
import sys

from app.auth import connect, normalize_email, password_hash


def create_admin(email: str, password: str):
    email = normalize_email(email)
    if not 8 <= len(password) <= 128:
        raise ValueError('Password must contain 8 to 128 characters')
    hashed = password_hash(password)
    with connect() as connection:
        row = connection.execute("INSERT INTO users(email_normalized,password_hash,role) VALUES (%s,%s,'ADMIN') ON CONFLICT (email_normalized) DO NOTHING RETURNING id", (email, hashed)).fetchone()
        if row is None:
            raise ValueError('Account already exists; no role was changed')
    return row['id']


def disable_user(email: str):
    with connect() as connection:
        row = connection.execute('UPDATE users SET disabled_at=COALESCE(disabled_at,now()),updated_at=now() WHERE email_normalized=%s RETURNING id', (normalize_email(email),)).fetchone()
        if row is None:
            raise ValueError('Account not found')
        connection.execute('UPDATE sessions SET revoked_at=COALESCE(revoked_at,now()) WHERE user_id=%s', (row['id'],))
    return row['id']


def main():
    parser = argparse.ArgumentParser(description='Local deployer identity administration')
    parser.add_argument('command', choices=('create-admin', 'disable-user'))
    parser.add_argument('--email', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'create-admin':
            if not sys.stdin.isatty():
                print('Admin creation requires an interactive terminal', file=sys.stderr)
                return 2
            password = getpass('Admin password: ')
            if password != getpass('Repeat password: '):
                raise ValueError('Passwords did not match')
            identifier = create_admin(args.email, password)
        else:
            identifier = disable_user(args.email)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Administration failed; verify database configuration', file=sys.stderr)
        return 1
    print(f'Account operation completed: {identifier}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


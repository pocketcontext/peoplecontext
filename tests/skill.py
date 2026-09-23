#!/usr/bin/env python3
"""Exercise the portable client against isolated synthetic HR records."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from integration import Server, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--write-schema', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='peoplecontext-skill-') as tmp:
        root = Path(tmp)
        s = Server(args.binary, root / 'server')
        try:
            password = 'SyntheticSkillPassword123!'
            account = s.create('agents', {'email': 'hr@example.test', 'name': 'Synthetic HR', 'password': password, 'passwordConfirm': password})
            s.create('hr_members', {'account': account['id']})
            s.create('agents', {'email': 'reader@example.test', 'name': 'Synthetic reader', 'password': password, 'passwordConfirm': password})
            schema = s.request('GET', '/api/context/schema', token=s.login('agents', 'hr@example.test', password))
            if args.write_schema:
                (ROOT/'skills/peoplecontext/references/schema.json').write_text(json.dumps({'tables': schema['tables']}, indent=2)+'\n')
            shutil.copytree(ROOT/'skills/peoplecontext', root/'installed')
            env = {**os.environ, 'PEOPLECONTEXT_URL': s.base, 'PEOPLECONTEXT_AGENT_EMAIL': 'hr@example.test', 'PEOPLECONTEXT_AGENT_PASSWORD': password, 'XDG_CACHE_HOME': str(root/'cache')}
            def cli(*command, code=0):
                r = subprocess.run([sys.executable, str(root/'installed/scripts/pc.py'), *command], env=env, capture_output=True, text=True)
                assert r.returncode == code, (command[0], r.returncode, r.stderr)
                assert password not in r.stdout+r.stderr
                assert 'eyJ' not in r.stdout+r.stderr
                return r.stdout
            assert json.loads(cli('whoami'))['id'] == account['id']
            cli('check')
            employee = json.loads(cli('create', 'employees', '{"name":"Synthetic worker"}'))
            salary = json.loads(cli('create', 'compensation', json.dumps({'employee': employee['id'], 'annual_salary_minor': 5500000, 'currency': 'EUR', 'effective_date': '2026-01-01 00:00:00.000Z'})))
            cli('update', 'employees', employee['id'], '{"department":"Test"}')
            assert json.loads(cli('get', 'employees', employee['id']))['department'] == 'Test'
            assert len(json.loads(cli('sql', 'SELECT * FROM compensation'))['rows']) == 1
            cli('create', 'hr_members', '{}', code=2)
            cli('create', 'employees', '{"name":"Forbidden identity", "work_email":"new@example.test"}', code=2)
            cli('update', 'employees', employee['id'], '{"work_email":"new@example.test"}', code=2)
            cli('batch', '[{"method":"POST","url":"/api/collections/employees/records","body":{"name":"Forbidden identity", "work_email":"new@example.test"}}]', code=2)
            cli('batch', '[{"method":"POST","url":"/api/collections/account_links/records","body":{}}]', code=2)
            cache = next((root/'cache/peoplecontext').glob('*.json'))
            assert cache.stat().st_mode & 0o777 == 0o600
            env['PEOPLECONTEXT_AGENT_EMAIL'] = 'reader@example.test'
            assert json.loads(cli('sql', 'SELECT * FROM compensation'))['rows'] == []
            cli('create', 'employees', '{"name":"Forbidden"}', code=1)
            cli('sql', 'SELECT * FROM account_links', code=1)
            cli('get', 'compensation', salary['id'], code=1)
            cli('logout')
            print('PASS: portable HR client, private token cache, filtered reads, HR writes, authority-write rejection')
        finally:
            s.stop(); s.log.close()

if __name__ == '__main__':
    main()

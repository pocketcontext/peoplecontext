#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Interactive synthetic PeopleContext demo. Run with uv run scripts/demo.py."""
import argparse
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('helen', 'dana', 'alice', 'bob', 'carol')
TABLES = ('employees', 'compensation', 'personal_details', 'hr_notes')
HELP = """Commands (inside this demo, not your shell):
  as helen|dana|alice|bob|carol    Switch agent; reuse its existing token
  show                          Query all four visible collections
  schema                        Show authenticated schema metadata
  sql SELECT ...                Run a single-line SQL query as current agent
  transfer bob alice|carol       Administrator changes Bob's manager
  hr off|on                     Administrator revokes/restores Helen's HR role
  bypass                        Check blocked policy/auth SQL and REST reads
  help                          Show commands
  quit                          Stop server and delete temporary demo data
"""


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'HTTP {status}: {message}')


class Demo:
    def __init__(self, binary, directory):
        self.root = Path(directory)
        self.binary = binary
        self.process = None
        self.log = None
        self.tokens = {}
        self.employees = {}
        self.lines = {}
        self.actor = 'alice'
        self.member = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        request = urllib.request.Request(
            self.base + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read()
                return (json.loads(raw) if raw else None), response.headers
        except urllib.error.HTTPError as error:
            # Never echo authentication bodies or credentials.
            with error:
                raw = error.read()
            message = 'Authentication failed' if 'auth-with-password' in path else 'Request rejected'
            if 'auth-with-password' not in path:
                try:
                    message = json.loads(raw).get('message', message)
                except (ValueError, AttributeError):
                    pass
            raise APIError(error.code, message) from None

    def login(self, collection, email, password):
        return self.request('POST', f'/api/collections/{collection}/auth-with-password',
                            {'identity': email, 'password': password})[0]['token']

    def create(self, collection, body, token):
        return self.request('POST', f'/api/collections/{collection}/records', body, token)[0]['id']

    def start(self):
        for name in ('pb_migrations', 'pb_hooks'):
            shutil.copytree(ROOT / name, self.root / name)
        shutil.copy2(ROOT / 'pocketcontext.json', self.root / 'pocketcontext.json')
        common = [str(self.binary), '--dir', str(self.root / 'pb_data'),
                  '--migrationsDir', str(self.root / 'pb_migrations'),
                  '--hooksDir', str(self.root / 'pb_hooks')]
        password = secrets.token_urlsafe(32)
        result = subprocess.run(common + ['superuser', 'upsert', 'admin@example.test', password],
                                cwd=self.root, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Temporary administrator provisioning failed; output withheld.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.log = (self.root / 'server.log').open('w')
        self.process = subprocess.Popen(common + ['serve', '--http', f'127.0.0.1:{port}'],
                                        cwd=self.root, stdout=self.log, stderr=self.log)
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError('Temporary server exited during startup.')
            try:
                self.request('GET', '/api/health')
                break
            except (OSError, APIError):
                time.sleep(.1)
        else:
            raise RuntimeError('Temporary server startup timed out.')
        self.admin = self.login('_superusers', 'admin@example.test', password)
        accounts = {}
        for name in NAMES:
            password = secrets.token_urlsafe(32)
            accounts[name] = self.create('agents', {
                'name': name.title(), 'email': name + '@example.test',
                'password': password, 'passwordConfirm': password}, self.admin)
            self.tokens[name] = self.login('agents', name + '@example.test', password)
        self.hr_account = accounts['helen']
        self.set_hr(True)
        hr = self.tokens['helen']
        schema = self.request('GET', '/api/context/schema', token=hr)[0]
        if schema.get('permissionModel') != 'filtered-snapshot':
            raise RuntimeError('Server must support filtered-snapshot permissions. Use the pinned binary.')
        for index, name in enumerate(NAMES):
            employee = self.create('employees', {'name': name.title(), 'job_title': 'Demo role',
                                                  'department': 'Synthetic demo'}, hr)
            self.employees[name] = employee
            self.create('account_links', {'account': accounts[name], 'employee': employee}, self.admin)
            self.create('compensation', {'employee': employee, 'annual_salary_minor': (index + 6) * 1000000,
                                         'currency': 'USD', 'effective_date': '2026-09-01 00:00:00.000Z'}, hr)
            self.create('personal_details', {'employee': employee, 'home_address': 'Synthetic home: ' + name,
                                             'emergency_contact': 'Synthetic contact'}, hr)
            self.create('hr_notes', {'employee': employee, 'body': 'Synthetic confidential note: ' + name}, hr)
        for child, manager in (('alice', 'dana'), ('bob', 'alice')):
            self.lines[child] = self.create('reporting_lines', {
                'employee': self.employees[child], 'manager': self.employees[manager]}, self.admin)

    def close(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log is not None:
            self.log.close()

    def set_hr(self, enabled):
        if enabled and self.member is None:
            self.member = self.create('hr_members', {'account': self.hr_account}, self.admin)
        elif not enabled and self.member is not None:
            self.request('DELETE', '/api/collections/hr_members/records/' + self.member, token=self.admin)
            self.member = None

    def transfer(self, manager):
        self.request('PATCH', '/api/collections/reporting_lines/records/' + self.lines['bob'],
                     {'manager': self.employees[manager]}, self.admin)

    def sql(self, statement):
        result, headers = self.request('POST', '/api/context/query', {'sql': statement}, self.tokens[self.actor])
        if headers.get('X-Context-Scope') != 'authorized-snapshot':
            raise RuntimeError('Missing authorized-snapshot response scope.')
        return result

    def show(self):
        fields = {
            'compensation': 't.annual_salary_minor, t.currency',
            'personal_details': 't.home_address, t.emergency_contact',
            'hr_notes': 't.body',
        }
        for table in TABLES:
            statement = 'SELECT name FROM employees ORDER BY name' if table == 'employees' else (
                f'SELECT e.name, {fields[table]} FROM {table} t '
                'JOIN employees e ON e.id=t.employee ORDER BY e.name')
            print(f'\n{table} ({self.actor}):')
            result = self.sql(statement)
            print(' | '.join(result['columns']))
            for row in result['rows']:
                print(' | '.join(str(value) for value in row))
            if not result['rows']:
                print('(no visible rows)')
            if result.get('truncated'):
                print('(results truncated)')

    def bypass(self):
        for table in ('account_links', 'hr_members', 'reporting_lines', 'agents'):
            try:
                self.sql(f'SELECT * FROM {table}')
            except APIError as error:
                if error.status not in (400, 401, 403, 404):
                    raise
                print(f'Blocked SQL {table}: HTTP {error.status}')
            else:
                raise RuntimeError(f'Unexpected SQL access to {table}')
        try:
            self.request('GET', '/api/collections/compensation/records', token=self.tokens[self.actor])
        except APIError as error:
            if error.status not in (401, 403, 404):
                raise
            print(f'Blocked REST compensation: HTTP {error.status}')
        else:
            raise RuntimeError('Unexpected direct REST read access')

    def command(self, line):
        words = line.split()
        if not words:
            return
        if words == ['help']:
            print(HELP)
        elif len(words) == 2 and words[0] == 'as' and words[1] in NAMES:
            self.actor = words[1]
            print(f'Using {self.actor}\'s original agent token.')
        elif words == ['show']:
            self.show()
        elif words == ['schema']:
            print(json.dumps(self.request('GET', '/api/context/schema', token=self.tokens[self.actor])[0], indent=2))
        elif words[0] == 'sql' and len(words) > 1:
            print(json.dumps(self.sql(line.split(None, 1)[1]), indent=2))
        elif len(words) == 3 and words[:2] == ['transfer', 'bob'] and words[2] in ('alice', 'carol'):
            self.transfer(words[2])
            print(f'Administrator transferred Bob to {words[2]}. Existing agent tokens are unchanged.')
        elif len(words) == 2 and words[0] == 'hr' and words[1] in ('on', 'off'):
            self.set_hr(words[1] == 'on')
            print(f'Administrator set Helen\'s HR membership {words[1]}. Her token is unchanged.')
        elif words == ['bypass']:
            self.bypass()
        else:
            print('Unknown command. Type help.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path,
                        default=ROOT.parent / 'pocketcontext/bin/pocketcontext',
                        help='Server built from POCKETCONTEXT_VERSION (defaults to workspace server binary)')
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve()
    if not binary.is_file():
        parser.error('Pinned server binary not found. Supply --binary /absolute/path/to/pocketcontext')
    try:
        with tempfile.TemporaryDirectory(prefix='peoplecontext-demo-') as directory:
            demo = Demo(binary, directory)
            try:
                print('Starting isolated server and provisioning synthetic records...', flush=True)
                demo.start()
                print('Ready. Dana → Alice → Bob; Carol is a separate branch; Helen is HR.')
                print('Everyone sees their own compensation; managers also see their reports; HR sees all.')
                print('Credentials stay in memory. All data is temporary. Queries use agent tokens.')
                print(HELP)
                while True:
                    try:
                        line = input(f'peoplecontext[{demo.actor}]> ').strip()
                    except EOFError:
                        break
                    if line in ('quit', 'exit'):
                        break
                    try:
                        demo.command(line)
                    except (APIError, OSError, RuntimeError) as error:
                        print(f'Error: {error}')
            finally:
                demo.close()
        print('Server stopped; temporary demo data deleted.')
    except KeyboardInterrupt:
        print('\nServer stopped; temporary demo data deleted.')
    except (APIError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Demo failed: {error}\n')


if __name__ == '__main__':
    main()

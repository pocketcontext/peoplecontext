#!/usr/bin/env python3
"""Synthetic HR acceptance tests using only HTTP and the Python standard library."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DENIED = (400, 401, 403, 404)


def records(collection, identifier=None):
    return '/api/collections/' + collection + '/records' + ('/' + identifier if identifier else '')


class Server:
    def __init__(self, binary, directory):
        self.root = Path(directory)
        for name in ('pb_migrations', 'pb_hooks'):
            shutil.copytree(ROOT / name, self.root / name)
        shutil.copy2(ROOT / 'pocketcontext.json', self.root / 'pocketcontext.json')
        self.common = [str(Path(binary).resolve()), '--dir', str(self.root / 'pb_data'),
                       '--migrationsDir', str(self.root / 'pb_migrations'), '--hooksDir', str(self.root / 'pb_hooks')]
        self.password = secrets.token_urlsafe(24)
        result = subprocess.run(self.common + ['superuser', 'upsert', 'admin@example.test', self.password],
                                cwd=self.root, capture_output=True)
        if result.returncode:
            raise AssertionError('Isolated database provisioning failed (output withheld to protect credentials)')
        self.process = None
        self.log = open(self.root / 'server.log', 'w+')
        try:
            self.start()
            self.admin = self.login('_superusers', 'admin@example.test', self.password)
        except BaseException:
            self.stop()
            self.log.close()
            raise

    def start(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.process = subprocess.Popen(self.common + ['serve', '--http', f'127.0.0.1:{port}'],
                                        cwd=self.root, stdout=self.log, stderr=self.log)
        for _ in range(150):
            try:
                self.request('GET', '/api/health')
                return
            except (OSError, AssertionError):
                if self.process.poll() is not None:
                    raise AssertionError('Server exited during startup; temporary log withheld')
                time.sleep(.1)
        raise AssertionError('Server startup timed out')

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None

    def request(self, method, path, body=None, token=None, expected=200, with_headers=False):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, raw, response_headers = response.code, response.read(), response.headers
        allowed = expected if isinstance(expected, tuple) else (expected,)
        detail = '(authentication response withheld)' if 'auth-with-password' in path else raw.decode()[:500]
        assert status in allowed, f'{method} {path}: expected {allowed}, received {status}: {detail}'
        result = json.loads(raw) if raw else None
        return (result, response_headers) if with_headers else result

    def login(self, collection, email, password):
        return self.request('POST', f'/api/collections/{collection}/auth-with-password',
                            {'identity': email, 'password': password})['token']

    def create(self, collection, body, token=None):
        return self.request('POST', records(collection), body, token or self.admin)

    def sql(self, token, query, expected=200, extra=None):
        return self.request('POST', '/api/context/query', {'sql': query, **(extra or {})}, token, expected)


def run(server):
    s = server
    names = ('hr', 'grand', 'manager', 'worker', 'other_grand', 'other_manager', 'other_worker', 'outsider')
    accounts, tokens, employees, links, salaries, personal, notes = {}, {}, {}, {}, {}, {}, {}
    for name in names:
        password = secrets.token_urlsafe(24)
        accounts[name] = s.create('agents', {'name': name, 'email': name + '@example.test',
                                             'password': password, 'passwordConfirm': password})
        tokens[name] = s.login('agents', name + '@example.test', password)
    hr_member = s.create('hr_members', {'account': accounts['hr']['id']})
    hr = tokens['hr']
    for index, name in enumerate(names):
        employees[name] = s.create('employees', {'name': name, 'job_title': 'Synthetic role', 'department': 'Test'}, hr)
        employee = employees[name]['id']
        links[name] = s.create('account_links', {'account': accounts[name]['id'], 'employee': employee})
        salaries[name] = s.create('compensation', {'employee': employee, 'annual_salary_minor': (index + 1) * 100000,
                                                   'currency': 'GBP', 'effective_date': '2030-01-01 00:00:00.000Z'}, hr)
        personal[name] = s.create('personal_details', {'employee': employee, 'home_address': 'Synthetic home ' + name,
                                                       'emergency_contact': 'Synthetic contact'}, hr)
        notes[name] = s.create('hr_notes', {'employee': employee, 'body': 'Confidential synthetic note ' + name}, hr)
    lines = {}
    for child, parent in (('manager', 'grand'), ('worker', 'manager'),
                          ('other_manager', 'other_grand'), ('other_worker', 'other_manager')):
        lines[child] = s.create('reporting_lines', {'employee': employees[child]['id'], 'manager': employees[parent]['id']})

    def visible(name, table='compensation'):
        result = s.sql(tokens[name], f'SELECT employee FROM {table}')
        return {row[0] for row in result['rows']}

    def ids(*members):
        return {employees[name]['id'] for name in members}

    expected = {'hr': ids(*names), 'grand': ids('manager', 'worker'), 'manager': ids('worker'),
                'worker': set(), 'other_grand': ids('other_manager', 'other_worker'),
                'other_manager': ids('other_worker'), 'other_worker': set(), 'outsider': set()}
    for name in names:
        assert visible(name) == expected[name], ('salary scope', name)
        assert visible(name, 'personal_details') == (ids(*names) if name == 'hr' else ids(name)), ('personal scope', name)
        assert visible(name, 'hr_notes') == (ids(*names) if name == 'hr' else set()), ('note scope', name)
        assert s.sql(tokens[name], 'SELECT count(*) FROM employees')['rows'] == [[len(names)]]
    result, headers = s.request('POST', '/api/context/query', {'sql': 'SELECT count(*) FROM compensation'}, tokens['grand'], with_headers=True)
    assert result['rows'] == [[2]]
    assert headers['X-Context-Scope'] == 'authorized-snapshot' and headers['X-Context-Snapshot-At']
    assert s.sql(tokens['grand'], 'WITH team AS (SELECT e.name, c.annual_salary_minor FROM employees e JOIN compensation c ON e.id=c.employee) SELECT name FROM team ORDER BY name')['rows'] == [['manager'], ['worker']]
    assert s.sql(tokens['grand'], 'SELECT sum(annual_salary_minor) FROM compensation')['rows'] == [[700000]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [(name, executor.submit(visible, name)) for _ in range(3) for name in names]
        for name, future in futures:
            assert future.result() == expected[name], ('concurrent isolation', name)
    print('PASS: directory, salary hierarchy, self/HR personal data, HR notes, joins, aggregates, concurrent isolation')

    schema = json.dumps(s.request('GET', '/api/context/schema', token=tokens['worker']))
    for table in ('account_links', 'hr_members', 'reporting_lines', 'agents', '_superusers', 'sqlite_schema'):
        assert '"' + table + '"' not in schema, ('schema disclosure', table)
        s.sql(tokens['worker'], f'SELECT * FROM {table}', expected=DENIED)
    for query in ('SELECT password FROM employees', 'SELECT rowid FROM compensation',
                  "ATTACH DATABASE ':memory:' AS source", 'DELETE FROM compensation',
                  'UPDATE compensation SET annual_salary_minor=0', 'SELECT load_extension(\'x\')',
                  'SELECT * FROM pragma_table_info(\'compensation\')', 'SELECT :requester',
                  'WITH hr_members AS (SELECT 1) SELECT * FROM compensation'):
        # A shadow CTE is harmless: it cannot modify the already-exported dataset.
        if query.startswith('WITH hr_members'):
            assert s.sql(tokens['worker'], query)['rows'] == []
        else:
            s.sql(tokens['worker'], query, expected=DENIED)
    spoof = s.sql(tokens['worker'], 'SELECT employee FROM compensation', extra={'requester': accounts['hr']['id']}, expected=400)
    assert 'rows' not in spoof, 'request body identity spoof succeeded'
    s.request('POST', '/api/context/query', {'sql': 'SELECT * FROM employees'}, expected=DENIED)
    print('PASS: schema boundary, source-only/auth tables, SQL mutation and identity bypass attempts')

    collections = {'employees': employees, 'compensation': salaries, 'personal_details': personal, 'hr_notes': notes,
                   'account_links': links, 'reporting_lines': lines, 'hr_members': {'hr': hr_member}, 'agents': accounts}
    for actor in ('worker', 'manager', 'hr'):
        for collection, objects in collections.items():
            record = next(iter(objects.values()))
            s.request('GET', records(collection), token=tokens[actor], expected=DENIED)
            s.request('GET', records(collection, record['id']), token=tokens[actor], expected=DENIED)
    for actor in ('worker', 'manager'):
        token = tokens[actor]
        for collection, objects in collections.items():
            record = next(iter(objects.values()))
            s.request('PATCH', records(collection, record['id']) + '?expand=employee,manager,account', {}, token, expected=DENIED)
            s.request('DELETE', records(collection, record['id']), token=token, expected=DENIED)
        s.request('POST', records('hr_members'), {'account': accounts[actor]['id']}, token, expected=DENIED)
        s.request('POST', records('employees'), {'name': 'Unauthorized'}, token, expected=DENIED)
        s.request('POST', '/api/batch', {'requests': [{'method': 'PATCH', 'url': records('compensation', salaries['worker']['id']), 'body': {'annual_salary_minor': 1}}]}, token, expected=400)
    for collection, objects in (('account_links', links), ('reporting_lines', lines), ('hr_members', {'hr': hr_member}), ('agents', accounts)):
        s.request('PATCH', records(collection, next(iter(objects.values()))['id']), {}, hr, expected=DENIED)
    # Valid create payloads avoid uniqueness/validation failures hiding a permissive rule.
    spare = s.create('employees', {'name': 'Write authorization probe'}, hr)
    for actor in ('worker', 'manager'):
        for collection, payload in (
            ('compensation', {'employee': spare['id'], 'annual_salary_minor': 100, 'currency': 'GBP', 'effective_date': '2030-01-01 00:00:00.000Z'}),
            ('personal_details', {'employee': spare['id'], 'home_address': 'Synthetic', 'emergency_contact': 'Synthetic'}),
            ('hr_notes', {'employee': spare['id'], 'body': 'Synthetic'})):
            s.request('POST', records(collection), payload, tokens[actor], expected=400)
    probe_password = secrets.token_urlsafe(24)
    for collection, payload in (
        ('agents', {'name': 'Forbidden provision', 'email': 'forbidden@example.test', 'password': probe_password, 'passwordConfirm': probe_password}),
        ('hr_members', {'account': accounts['worker']['id']}),
        ('reporting_lines', {'employee': spare['id'], 'manager': employees['grand']['id']}),
        ('account_links', {'account': accounts['worker']['id'], 'employee': spare['id']})):
        s.request('POST', records(collection), payload, hr, expected=403)
    for collection, objects in (('account_links', links), ('reporting_lines', lines), ('hr_members', {'hr': hr_member}), ('agents', accounts)):
        s.request('DELETE', records(collection, next(iter(objects.values()))['id']), token=hr, expected=403)
    s.request('DELETE', records('employees', spare['id']), token=hr, expected=204)
    # Even a permitted write must not expand policy records or other locked records.
    expanded = s.request('PATCH', records('compensation', salaries['worker']['id']) + '?expand=employee,employee.account_links_via_employee.account', {'currency': 'USD'}, hr)
    assert not expanded.get('expand'), 'locked relation expanded in write response'
    for invalid in ({'annual_salary_minor': -1}, {'annual_salary_minor': 1.5}, {'currency': 'usd'}):
        s.request('PATCH', records('compensation', salaries['worker']['id']), invalid, hr, expected=400)
    # Required non-cascading relations prevent HR deleting policy-bearing identities.
    s.request('DELETE', records('employees', employees['worker']['id']), token=hr, expected=400)
    assert visible('manager') == ids('worker')
    # PocketBase accepts subscriptions but must suppress record events under locked view/list rules.
    for actor in ('worker', 'manager', 'hr', 'admin'):
        stream = urllib.request.urlopen(s.base + '/api/realtime', timeout=1)
        try:
            event = []
            while True:
                line = stream.readline().decode().strip()
                if not line:
                    break
                event.append(line)
            payload = json.loads(next(line[5:].lstrip() for line in event if line.startswith('data:')))
            subscriptions = [collection + '/*' for collection in
                             ('compensation', 'personal_details', 'hr_notes', 'account_links', 'hr_members', 'reporting_lines')]
            subscriptions += ['compensation/' + salaries['worker']['id'],
                              'personal_details/' + personal['worker']['id'], 'hr_notes/' + notes['worker']['id'],
                              'account_links/' + links['worker']['id'], 'hr_members/' + hr_member['id'],
                              'reporting_lines/' + lines['worker']['id']]
            s.request('POST', '/api/realtime', {'clientId': payload['clientId'], 'subscriptions': subscriptions},
                      s.admin if actor == 'admin' else tokens[actor], expected=204)
            s.request('PATCH', records('compensation', salaries['worker']['id']), {'currency': 'EUR'}, hr)
            s.request('PATCH', records('personal_details', personal['worker']['id']), {'emergency_contact': 'Updated synthetic contact'}, hr)
            s.request('PATCH', records('hr_notes', notes['worker']['id']), {'body': 'Updated confidential synthetic note'}, hr)
            s.request('PATCH', records('account_links', links['worker']['id']), {}, s.admin)
            s.request('PATCH', records('hr_members', hr_member['id']), {}, s.admin)
            s.request('PATCH', records('reporting_lines', lines['worker']['id']), {}, s.admin)
            timed_out = False
            try:
                incoming = stream.readline()
            except (TimeoutError, socket.timeout):
                timed_out = True
                incoming = b''
            if actor == 'admin':
                assert incoming, 'realtime admin positive control received no change'
            else:
                assert timed_out, 'private realtime delivered data or unexpectedly disconnected'
        finally:
            stream.close()
    print('PASS: REST list/view, role escalation, writes, batches, expansions, realtime and deletion protection')

    start = time.monotonic()
    s.request('PATCH', records('reporting_lines', lines['manager']['id']), {'manager': employees['other_manager']['id']}, s.admin)
    assert visible('grand') == set()
    assert visible('manager') == ids('worker')
    assert visible('other_manager') == ids('manager', 'worker', 'other_worker')
    assert visible('other_grand') == ids('other_manager', 'other_worker', 'manager', 'worker')
    print(f'PASS: cross-branch transfer revokes old chain and grants new chain on next requests ({time.monotonic() - start:.3f}s)')
    s.request('DELETE', records('hr_members', hr_member['id']), token=s.admin, expected=204)
    assert visible('hr') == set() and visible('hr', 'hr_notes') == set()
    assert visible('hr', 'personal_details') == ids('hr')
    s.request('PATCH', records('compensation', salaries['worker']['id']), {}, hr, expected=DENIED)
    hr_member = s.create('hr_members', {'account': accounts['hr']['id']})
    assert visible('hr') == ids(*names)
    # Remap linked identities; existing login tokens must see fresh policy.
    s.request('DELETE', records('account_links', links['manager']['id']), token=s.admin, expected=204)
    s.request('PATCH', records('account_links', links['outsider']['id']), {'account': accounts['manager']['id']}, s.admin)
    links['manager'] = s.create('account_links', {'account': accounts['outsider']['id'], 'employee': employees['manager']['id']})
    assert visible('manager') == set() and visible('outsider') == ids('worker')
    assert visible('manager', 'personal_details') == ids('outsider')
    print('PASS: HR removal/restoration and account remapping immediately affect existing tokens')

    s.request('POST', records('reporting_lines'), {'employee': employees['grand']['id'], 'manager': employees['grand']['id']}, s.admin, expected=400)
    s.request('POST', records('reporting_lines'), {'employee': employees['other_grand']['id'], 'manager': employees['worker']['id']}, s.admin, expected=400)
    s.request('PATCH', records('reporting_lines', lines['manager']['id']), {'manager': employees['worker']['id']}, s.admin, expected=400)
    # Independent nodes avoid existing unique-employee edges.
    pair = [s.create('employees', {'name': 'Cycle ' + name}, hr) for name in ('A', 'B')]
    bodies = [{'employee': pair[0]['id'], 'manager': pair[1]['id']}, {'employee': pair[1]['id'], 'manager': pair[0]['id']}]
    s.request('POST', '/api/batch', {'requests': [{'method': 'POST', 'url': records('reporting_lines'), 'body': body} for body in bodies]}, s.admin, expected=400)
    remaining = s.request('GET', records('reporting_lines'), token=s.admin)['items']
    assert all(row['employee'] not in {node['id'] for node in pair} for row in remaining), 'failed cycle batch was not rolled back'
    barrier = threading.Barrier(2)
    def opposing_edge(body):
        barrier.wait(timeout=10)
        return s.request('POST', records('reporting_lines'), body, s.admin, expected=(200, 400, 503))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(opposing_edge, bodies))
    remaining = s.request('GET', records('reporting_lines'), token=s.admin)['items']
    assert sum(row['employee'] in {node['id'] for node in pair} for row in remaining) == 1, 'concurrent opposing edges created a cycle or both failed'
    # Replacing an edge's employee must validate the new graph and exclude the old edge.
    edge = next(row for row in remaining if row['employee'] in {node['id'] for node in pair})
    s.request('PATCH', records('reporting_lines', edge['id']), {'employee': edge['manager'], 'manager': edge['employee']}, s.admin)
    s.request('PATCH', records('reporting_lines', edge['id']), {'employee': edge['employee'], 'manager': edge['employee']}, s.admin, expected=400)
    # A valid write followed by an invalid salary must roll back as a whole.
    s.request('POST', '/api/batch', {'requests': [
        {'method': 'PATCH', 'url': records('compensation', salaries['worker']['id']), 'body': {'annual_salary_minor': 999}},
        {'method': 'PATCH', 'url': records('compensation', salaries['other_worker']['id']), 'body': {'annual_salary_minor': -1}}]}, hr, expected=400)
    assert s.sql(hr, f"SELECT annual_salary_minor FROM compensation WHERE id='{salaries['worker']['id']}'")['rows'] == [[400000]]
    print('PASS: self/cyclic hierarchy, batch cycle rollback, concurrent opposing edges and HR write rollback')

    s.stop()
    config_path = s.root / 'pocketcontext.json'
    config = json.loads(config_path.read_text())
    config['snapshot']['maxRows'] = 1
    config_path.write_text(json.dumps(config))
    s.start()
    result = s.sql(hr, 'SELECT count(*) FROM compensation', expected=413)
    assert 'rows' not in result, 'oversized snapshot returned partial analytical results'
    print('PASS: oversized snapshot fails without partial results')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='peoplecontext-test-') as directory:
        server = None
        try:
            server = Server(args.binary, directory)
            run(server)
        finally:
            if server is not None:
                server.stop()
                server.log.close()
    print('PeopleContext integration checks passed.')


if __name__ == '__main__':
    main()

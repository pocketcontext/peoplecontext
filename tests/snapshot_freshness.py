#!/usr/bin/env python3
"""Synthetic read-after-write checks for reused filtered SQL snapshots.

The pinned server retains each requester's private snapshot while the source
database is unchanged. These checks prove reuse with X-Context-Snapshot-At
(unchanged on a cache hit), then require HR role grants and revocations,
transfers, account-link changes, record updates and account disablement to be
visible on the very next read, with compensation, personal details and HR notes
kept to their separate policies and no rows leaking between requesters.
"""
import argparse
import concurrent.futures
import secrets
import tempfile
from integration import Server, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='peoplecontext-fresh-') as directory:
        s = None
        try:
            s = Server(args.binary, directory)
            run(s)
        finally:
            if s is not None:
                s.stop()
                s.log.close()
    print('Snapshot reuse read-after-write, policy separation, transfer and revocation checks passed')


def run(s):
    names = ('hr', 'candidate', 'manager_a', 'manager_b', 'worker', 'outsider')
    accounts, tokens, employees, links = {}, {}, {}, {}
    for name in names:
        password = secrets.token_urlsafe(24)
        accounts[name] = s.create('agents', {'name': name, 'email': name + '@example.test',
                                             'password': password, 'passwordConfirm': password})
        tokens[name] = s.login('agents', name + '@example.test', password)
    s.create('hr_members', {'account': accounts['hr']['id']})
    hr = tokens['hr']
    salaries = {}
    for index, name in enumerate(names):
        employees[name] = s.create('employees', {'name': name, 'job_title': 'Synthetic', 'department': 'Test'}, hr)
        employee = employees[name]['id']
        links[name] = s.create('account_links', {'account': accounts[name]['id'], 'employee': employee})
        salaries[name] = s.create('compensation', {'employee': employee, 'annual_salary_minor': (index + 1) * 100000,
                                                   'currency': 'GBP', 'effective_date': '2030-01-01 00:00:00.000Z'}, hr)
        s.create('personal_details', {'employee': employee, 'home_address': 'Synthetic home ' + name,
                                      'emergency_contact': 'Synthetic contact'}, hr)
        s.create('hr_notes', {'employee': employee, 'body': 'Synthetic note ' + name}, hr)
    line = s.create('reporting_lines', {'employee': employees['worker']['id'], 'manager': employees['manager_a']['id']})

    schema = s.request('GET', '/api/context/schema', token=tokens['worker'])
    assert schema['permissionModel'] == 'filtered-snapshot' and 'snapshot' not in schema, 'background snapshots must stay disabled'
    assert schema['snapshotLimits']['maxConcurrentPerRequester'] == 0, 'per-requester admission must stay disabled'

    def sql(name, query):
        result, headers = s.request('POST', '/api/context/query', {'sql': query}, tokens[name], with_headers=True)
        assert headers['X-Context-Scope'] == 'authorized-snapshot' and not result['truncated']
        return result['rows'], headers['X-Context-Snapshot-At']

    def ids(*members):
        return {employees[name]['id'] for name in members}

    def view(name):
        """Employee ids visible per private table, plus visible salaries."""
        rows, at = sql(name, "SELECT 'compensation', employee, annual_salary_minor FROM compensation UNION ALL "
                             "SELECT 'personal_details', employee, NULL FROM personal_details UNION ALL "
                             "SELECT 'hr_notes', employee, NULL FROM hr_notes")
        result = {'compensation': set(), 'personal_details': set(), 'hr_notes': set(), 'salary': {}}
        for table, employee, salary in rows:
            result[table].add(employee)
            if table == 'compensation':
                result['salary'][employee] = salary
        return result, at

    def warm(name):
        first, at = view(name)
        again, at2 = view(name)
        assert first == again and at == at2, ('expected a reused snapshot for an unchanged source', name)
        return first, at

    def fresh(name, before):
        result, at = view(name)
        assert at != before, ('a committed write must invalidate the reused snapshot', name)
        return result

    def warm_all():
        return {name: warm(name)[1] for name in names}

    def check(name, result, compensation, personal, notes):
        assert result['compensation'] == compensation, ('compensation', name)
        assert result['personal_details'] == personal, ('personal_details', name)
        assert result['hr_notes'] == notes, ('hr_notes', name)

    everyone = ids(*names)
    baseline = {'hr': (everyone, everyone, everyone), 'candidate': (ids('candidate'), ids('candidate'), set()),
                'manager_a': (ids('manager_a', 'worker'), ids('manager_a'), set()),
                'manager_b': (ids('manager_b'), ids('manager_b'), set()),
                'worker': (ids('worker'), ids('worker'), set()), 'outsider': (ids('outsider'), ids('outsider'), set())}
    for name in names:
        check(name, warm(name)[0], *baseline[name])

    # HR grant: the next read by the grantee reveals all private tables; nobody else changes.
    stamps = warm_all()
    grant = s.create('hr_members', {'account': accounts['candidate']['id']})
    for name in names:
        result = fresh(name, stamps[name])
        check(name, result, *((everyone, everyone, everyone) if name == 'candidate' else baseline[name]))

    # HR revocation: the cached HR-wide copy is never served again.
    stamps = warm_all()
    s.request('DELETE', records('hr_members', grant['id']), token=s.admin, expected=204)
    for name in names:
        check(name, fresh(name, stamps[name]), *baseline[name])

    # Transfer: the old chain loses salary access and the new chain gains it; managers never gain personal details.
    stamps = warm_all()
    s.request('PATCH', records('reporting_lines', line['id']), {'manager': employees['manager_b']['id']}, s.admin)
    transferred = dict(baseline, manager_a=(ids('manager_a'), ids('manager_a'), set()),
                       manager_b=(ids('manager_b', 'worker'), ids('manager_b'), set()))
    for name in names:
        check(name, fresh(name, stamps[name]), *transferred[name])

    # An ordinary HR record update is visible to every authorized reader on the next read.
    stamps = warm_all()
    s.request('PATCH', records('compensation', salaries['worker']['id']), {'annual_salary_minor': 7654321}, hr)
    worker = employees['worker']['id']
    for name in ('hr', 'manager_b', 'worker'):
        assert fresh(name, stamps[name])['salary'][worker] == 7654321, name
    for name in ('candidate', 'manager_a', 'outsider'):
        assert worker not in fresh(name, stamps[name])['salary'], name

    # Removing the worker's account link removes self access, including from cached copies; relinking restores it.
    stamps = warm_all()
    s.request('DELETE', records('account_links', links['worker']['id']), token=s.admin, expected=204)
    check('worker', fresh('worker', stamps['worker']), set(), set(), set())
    check('manager_b', fresh('manager_b', stamps['manager_b']), *transferred['manager_b'])
    stamps = warm_all()
    s.create('account_links', {'account': accounts['worker']['id'], 'employee': worker})
    check('worker', fresh('worker', stamps['worker']), *transferred['worker'])

    # Concurrent reads from several sessions of one account are admitted (no per-requester 429).
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: view('manager_b')[0]['compensation'], range(8)))
    assert all(result == transferred['manager_b'][0] for result in results)

    # Disablement is enforced before any retained snapshot can be served.
    warm('outsider')
    s.request('PATCH', records('agents', accounts['outsider']['id']), {'disabled': True}, s.admin)
    s.request('POST', '/api/context/query', {'sql': 'SELECT id FROM employees'}, tokens['outsider'], expected=(401, 403))


if __name__ == '__main__':
    main()

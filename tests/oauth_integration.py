#!/usr/bin/env python3
"""Exercise PocketBase's Google exchange with a local provider and synthetic users."""
import argparse
import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import time
import urllib.parse
from unittest.mock import patch

from integration import Server, records, DENIED
import tempfile


@contextlib.contextmanager
def google_fixture():
    codes, tokens = {}, {}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != '/token':
                return self.reply(404, {})
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode())
            get = lambda key: form.get(key, [''])[0]
            pending = codes.get(get('code'))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(get('code_verifier').encode()).digest()).decode().rstrip('=')
            if not pending or challenge != pending['challenge'] or get('redirect_uri') != REDIRECT:
                return self.reply(400, {'error': 'invalid_grant'})
            if get('grant_type') != 'authorization_code':
                return self.reply(400, {'error': 'unsupported_grant_type'})
            del codes[get('code')]
            token = secrets.token_urlsafe(24)
            tokens[token] = pending['user']
            self.reply(200, {'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600})

        def do_GET(self):
            user = tokens.get(self.headers.get('Authorization', '').removeprefix('Bearer '))
            if self.path != '/userinfo' or user is None:
                return self.reply(401, {})
            self.reply(200, user)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', codes
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


REDIRECT = 'http://127.0.0.1:8765/callback'


def check(binary, jit=True):
    with tempfile.TemporaryDirectory(prefix='peoplecontext-oauth-') as directory, google_fixture() as (url, codes):
        s = Server(binary, directory)
        try:
            collection = s.request('GET', '/api/collections/agents', token=s.admin)
            assert collection['updateRule'] is None
            assert collection['authToken']['duration'] == 604800
            s.request('PATCH', '/api/collections/agents', {'oauth2': {'enabled': True, 'providers': [{
                'name': 'google', 'clientId': 'synthetic', 'clientSecret': 'synthetic',
                'authURL': url + '/authorize', 'tokenURL': url + '/token', 'userInfoURL': url + '/userinfo',
            }]}}, s.admin)

            def exchange(email='worker@example.test', *, hd='example.test', verified=True, expected=200, extra=None, subject=None):
                provider = s.request('GET', '/api/collections/agents/auth-methods')['oauth2']['providers'][0]
                code = secrets.token_urlsafe(24)
                user = {'sub': subject or email.lower(), 'email': email, 'email_verified': verified, 'name': 'Trusted Google name'}
                if hd is not None:
                    user['hd'] = hd
                codes[code] = {'challenge': provider['codeChallenge'], 'user': user}
                return s.request('POST', '/api/collections/agents/auth-with-oauth2', {
                    'provider': 'google', 'code': code, 'codeVerifier': provider['codeVerifier'],
                    'redirectURL': REDIRECT, 'createData': extra or {},
                }, expected=expected)

            if not jit:
                existing = s.create('agents', {'name': 'Existing', 'email': 'worker@example.test',
                    'password': 'SyntheticPassword123!', 'passwordConfirm': 'SyntheticPassword123!'})
                s.create('employees', {'name': 'Existing', 'work_email': 'worker@example.test'})
                exchange('stranger@example.test', expected=DENIED)
                assert exchange()['record']['id'] == existing['id']
                assert s.request('GET', records('account_links'), token=s.admin)['totalItems'] == 0
                return

            for email, hd, verified in [('evil@example.test', None, True), ('evil@example.test', 'elsewhere.test', True),
                                        ('evil@example.test', 'example.test', False), ('evil@example.test', 'example.test', 'true'),
                                        ('evil@elsewhere.test', 'example.test', True), ('evil@example.test.evil', 'example.test', True)]:
                exchange(email, hd=hd, verified=verified, expected=DENIED)
            assert s.request('GET', records('agents'), token=s.admin)['totalItems'] == 0
            s.request('POST', records('agents') + '?context=oauth2', {'name': 'spoof', 'email': 'spoof@example.test',
                'password': 'SyntheticPassword123!', 'passwordConfirm': 'SyntheticPassword123!'}, expected=DENIED)
            employee = s.create('employees', {'name': 'Synthetic worker', 'work_email': 'WORKER@example.test'})
            s.request('POST', records('employees'), {'name': 'Duplicate', 'work_email': 'worker@EXAMPLE.test'}, s.admin, DENIED)
            salary = s.create('compensation', {'employee': employee['id'], 'annual_salary_minor': 10000,
                'currency': 'EUR', 'effective_date': '2030-01-01 00:00:00.000Z'})
            s.create('personal_details', {'employee': employee['id'], 'home_address': 'Synthetic home'})
            s.create('hr_notes', {'employee': employee['id'], 'body': 'Synthetic confidential note'})
            auth = exchange(extra={'id': 'forgedid1234567', 'email': 'evil@example.test', 'name': 'Untrusted',
                                   'disabled': True, 'password': 'ForgedPassword123!', 'passwordConfirm': 'ForgedPassword123!'})
            account, token = auth['record'], auth['token']
            assert account['email'] == 'worker@example.test' and account['name'] == 'Trusted Google name'
            assert account['id'] != 'forgedid1234567' and not account['disabled'] and account['verified']
            assert exchange()['record']['id'] == account['id']
            claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
            assert 604790 <= claims['exp'] - time.time() <= 604800
            assert s.sql(token, 'SELECT id FROM compensation')['rows'] == [[salary['id']]]
            assert len(s.sql(token, 'SELECT id FROM personal_details')['rows']) == 1
            assert s.sql(token, 'SELECT id FROM hr_notes')['rows'] == []
            for table in ['account_links', 'hr_members', 'reporting_lines']:
                s.sql(token, 'SELECT * FROM ' + table, DENIED)
            s.sql(token, 'SELECT work_email FROM employees', DENIED)
            assert s.request('GET', records('hr_members'), token=s.admin)['totalItems'] == 0
            link = s.request('GET', records('account_links'), token=s.admin)['items'][0]
            assert link['account'] == account['id'] and link['employee'] == employee['id']
            s.request('PATCH', records('employees', employee['id']), {'work_email': 'different@example.test'}, s.admin, DENIED)
            outsider = exchange('outsider@example.test')
            assert len(s.sql(outsider['token'], 'SELECT id FROM employees')['rows']) == 1
            for table in ['compensation', 'personal_details', 'hr_notes']:
                assert s.sql(outsider['token'], 'SELECT id FROM ' + table)['rows'] == []
            assert s.request('GET', records('account_links'), token=s.admin)['totalItems'] == 1
            # An operator can enrich the existing directory later; next Google login links the same account.
            other_employee = s.create('employees', {'name': 'Synthetic outsider', 'work_email': 'outsider@example.test'})
            assert exchange('outsider@example.test')['record']['id'] == outsider['record']['id']
            assert s.request('GET', records('account_links'), token=s.admin)['totalItems'] == 2
            # Password authentication never establishes authority from an email claim.
            manual = s.create('agents', {'name': 'Manual', 'email': 'MANUAL@example.test', 'verified': True,
                'password': 'SyntheticPassword123!', 'passwordConfirm': 'SyntheticPassword123!'})
            manual_employee = s.create('employees', {'name': 'Manual', 'work_email': 'manual@example.test'})
            manual_token = s.login('agents', 'MANUAL@example.test', 'SyntheticPassword123!')
            assert s.request('GET', records('account_links'), token=s.admin)['totalItems'] == 2
            s.request('PATCH', records('agents', manual['id']), {'disabled': True}, s.admin)
            exchange('manual@example.test', expected=DENIED)
            s.request('POST', '/api/collections/agents/auth-with-password',
                {'identity': 'MANUAL@example.test', 'password': 'SyntheticPassword123!'}, expected=DENIED)
            s.request('PATCH', records('agents', manual['id']), {'disabled': False}, s.admin)
            assert exchange('manual@example.test')['record']['id'] == manual['id']
            s.request('GET', '/api/context/schema', token=manual_token, expected=(401, 403))
            assert s.request('GET', records('account_links'), token=s.admin)['totalItems'] == 3
            # HR still cannot assign identity or disclose hidden identity on its ordinary write responses.
            s.create('hr_members', {'account': outsider['record']['id']})
            hr = outsider['token']
            for method, path, body in [('POST', records('employees'), {'name': 'New', 'work_email': 'new@example.test'}),
                                       ('PATCH', records('employees', employee['id']), {'work_email': 'evil@example.test'})]:
                # PocketBase strips hidden fields before applying write rules.
                # The ordinary write may succeed, but cannot set identity data.
                result = s.request(method, path, body, hr)
                assert 'work_email' not in result
                stored = s.request('GET', records('employees', result['id']), token=s.admin)
                assert stored['work_email'] == ('' if method == 'POST' else 'WORKER@example.test')
                batch = s.request('POST', '/api/batch', {'requests': [{'method': method, 'url': path, 'body': body}]}, hr)
                assert 'work_email' not in batch[0]['body']
                stored = s.request('GET', records('employees', batch[0]['body']['id']), token=s.admin)
                assert stored['work_email'] == ('' if method == 'POST' else 'WORKER@example.test')
            written = s.request('PATCH', records('employees', employee['id']), {'job_title': 'Updated synthetic title'}, hr)
            assert 'work_email' not in written
            s.request('GET', records('employees', employee['id']), token=hr, expected=DENIED)
            # Existing explicit links cannot be reassigned automatically.
            conflicting = s.create('agents', {'name': 'Conflict', 'email': 'conflict@example.test',
                'password': 'SyntheticPassword123!', 'passwordConfirm': 'SyntheticPassword123!'})
            conflict_employee = s.create('employees', {'name': 'Conflict', 'work_email': 'conflict@example.test'})
            s.request('DELETE', records('account_links', link['id']), token=s.admin, expected=204)
            s.create('account_links', {'account': account['id'], 'employee': conflict_employee['id']})
            exchange(expected=DENIED)
            exchange('conflict@example.test', expected=DENIED)
            # Account revocation covers auth, REST, SQL, schema, batch, and token refresh.
            s.request('PATCH', records('agents', outsider['record']['id']), {'disabled': True}, token, DENIED)
            s.request('DELETE', records('agents', outsider['record']['id']), token=s.admin, expected=DENIED)
            s.request('PATCH', records('agents', outsider['record']['id']), {'disabled': True}, s.admin)
            exchange('outsider@example.test', expected=DENIED, extra={'disabled': False})
            for method, path, body in [('GET', '/api/context/schema', None), ('POST', '/api/context/query', {'sql': 'SELECT * FROM employees'}),
                                      ('GET', records('employees'), None), ('POST', '/api/collections/agents/auth-refresh', {}),
                                      ('POST', '/api/batch', {'requests': []}),
                                      ('POST', '/api/realtime', {'clientId': 'invalid', 'subscriptions': ['employees']}),
                                      ('POST', records('employees'), {'name': 'Denied'})]:
                s.request(method, path, body, hr, (401, 403))
            s.request('PATCH', records('agents', outsider['record']['id']), {'disabled': False}, s.admin)
            s.request('GET', '/api/context/schema', token=hr, expected=(401, 403))
            assert exchange('outsider@example.test')['record']['id'] == outsider['record']['id']
        finally:
            s.stop()
            s.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with patch.dict(os.environ, {'PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'example.test'}):
        check(args.binary)
    with patch.dict(os.environ, {'PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN': ''}):
        check(args.binary, jit=False)
    print('PeopleContext Google Workspace JIT, identity linking, privacy, and revocation checks passed')


if __name__ == '__main__':
    main()

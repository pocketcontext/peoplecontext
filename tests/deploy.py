#!/usr/bin/env python3
"""Verify first-install and restart configuration using isolated synthetic data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from integration import ROOT, Server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    binary = str(Path(parser.parse_args().binary).resolve())
    client_secret = secrets.token_urlsafe(24)
    rotated_secret = secrets.token_urlsafe(24)
    env = {
        'PEOPLECONTEXT_GOOGLE_CLIENT_ID': 'synthetic-client.apps.googleusercontent.com',
        'PEOPLECONTEXT_GOOGLE_CLIENT_SECRET': client_secret,
        'PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'example.test',
        'PEOPLECONTEXT_RATE_LIMITS': 'false',
        'BASE_URL': 'https://people.example.test',
    }
    with tempfile.TemporaryDirectory(prefix='peoplecontext-deploy-') as directory, patch.dict(os.environ, env):
        server = Server(binary, directory)
        try:
            def collection():
                return server.request('GET', '/api/collections/agents', token=server.admin)

            configured = collection()
            assert configured['oauth2']['enabled']
            assert configured['oauth2']['providers'][0]['clientId'] == env['PEOPLECONTEXT_GOOGLE_CLIENT_ID']
            assert configured['authToken']['duration'] == 604800
            assert configured['createRule'] == "@request.context = 'oauth2'"
            assert configured['updateRule'] is None and configured['authRule'] == 'disabled = false'
            assert configured['passwordAuth']['enabled']
            assert client_secret not in json.dumps(configured)
            settings = server.request('GET', '/api/settings', token=server.admin)
            assert settings['meta']['appURL'] == env['BASE_URL']
            assert not settings['rateLimits']['enabled']

            # Operator-defined provider options survive credential rotation at restart.
            server.request('PATCH', '/api/collections/agents', {'oauth2': {'enabled': True, 'providers': [{
                'name': 'google', 'clientId': env['PEOPLECONTEXT_GOOGLE_CLIENT_ID'], 'clientSecret': client_secret,
                'authURL': 'https://accounts.google.com/o/oauth2/auth',
                'userInfoURL': 'https://www.googleapis.com/oauth2/v3/userinfo',
            }]}}, server.admin)
            server.stop()
            with patch.dict(os.environ, {'PEOPLECONTEXT_GOOGLE_CLIENT_ID': 'rotated.apps.googleusercontent.com',
                                         'PEOPLECONTEXT_GOOGLE_CLIENT_SECRET': rotated_secret}):
                server.start()
                configured = collection()
                provider = configured['oauth2']['providers'][0]
                assert provider['clientId'] == 'rotated.apps.googleusercontent.com'
                assert provider['authURL'] == 'https://accounts.google.com/o/oauth2/auth'
                assert provider['userInfoURL'] == 'https://www.googleapis.com/oauth2/v3/userinfo'
                assert configured['updateRule'] is None and configured['authToken']['duration'] == 604800
            server.stop()
            with patch.dict(os.environ, {'PEOPLECONTEXT_GOOGLE_CLIENT_ID': '', 'PEOPLECONTEXT_GOOGLE_CLIENT_SECRET': ''}):
                server.start()
                assert collection()['oauth2']['providers'][0]['clientId'] == 'rotated.apps.googleusercontent.com'
            server.stop()
            server.log.flush()
            log = (Path(directory) / 'server.log').read_text()
            assert client_secret not in log and rotated_secret not in log and server.password not in log
        finally:
            server.stop()
            server.log.close()

    # Fail before serving for incomplete credentials, whitespace, and invalid Workspace domains.
    for invalid in ({'PEOPLECONTEXT_GOOGLE_CLIENT_SECRET': ''}, {'PEOPLECONTEXT_GOOGLE_CLIENT_ID': ''},
                    {'PEOPLECONTEXT_GOOGLE_CLIENT_SECRET': 'secret with spaces'},
                    {'PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'https://example.test'},
                    {'PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN': 'EXAMPLE.TEST'}):
        with tempfile.TemporaryDirectory(prefix='peoplecontext-invalid-deploy-') as directory:
            root = Path(directory)
            for name in ('pb_hooks', 'pb_migrations'):
                shutil.copytree(ROOT / name, root / name)
            shutil.copy2(ROOT / 'pocketcontext.json', root / 'pocketcontext.json')
            result = subprocess.run([binary, 'migrate', 'up', '--dir', str(root / 'pb_data')],
                                    cwd=root, env={**os.environ, **env, **invalid}, capture_output=True, text=True)
            assert result.returncode != 0, 'invalid configuration unexpectedly accepted'
            assert client_secret not in result.stdout + result.stderr
    print('PASS: first-install OAuth environment, settings, rotation, option preservation, secret redaction, invalid config')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Saved login configuration and isolation tests using synthetic credentials."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from peoplecontext_client import cli

class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cfg = {'url': 'https://app.example.test', 'email': 'member@example.test', 'password': None}

    def login(self, cfg=None):
        cfg = cfg or self.cfg
        session = {**cfg, 'token': 'synthetic-token'}
        session.pop('password')
        with patch.object(cli, 'google_login', return_value=session), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run(cli.parse(['login', '--google', '--url', cfg['url'], '--email', cfg['email']])), 0)

    def test_login_saves_settings_and_reuses_session_without_environment(self):
        self.login()
        self.assertEqual(cli.config(), self.cfg)
        self.assertEqual(cli.load_session(self.cfg)['token'], 'synthetic-token')
        self.assertEqual(json.loads(cli.default_file().read_text()), {k:self.cfg[k] for k in ('url','email')})
        self.assertEqual(stat.S_IMODE(cli.default_file().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(cli.default_file().parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(cli.session_file(self.cfg).stat().st_mode), 0o600)
        with patch.object(cli, 'send', return_value=(200, {'ok': True})) as send:
            cli.call(cli.config(), 'GET', '/synthetic')
            self.assertEqual(send.call_args.args[4], 'synthetic-token')

    def test_precedence_and_account_isolation(self):
        self.login()
        with patch.dict(os.environ, {cli.ENV[0]: 'https://env.example.test', cli.ENV[1]:'env@example.test'}):
            self.assertEqual(cli.config()['url'], 'https://env.example.test')
            self.assertIsNone(cli.load_session(cli.config()))
            for argv in (['--url','https://flag.example.test','--email','flag@example.test','whoami'], ['whoami','--url','https://flag.example.test','--email','flag@example.test']):
                cfg = cli.config(args=cli.parse(argv))
                self.assertEqual((cfg['url'], cfg['email']), ('https://flag.example.test','flag@example.test'))
                self.assertIsNone(cli.load_session(cfg))
        self.assertEqual(cli.config(), self.cfg)

    def test_failed_login_keeps_previous_default(self):
        self.login()
        before = cli.default_file().read_bytes()
        with patch.object(cli, 'google_login', side_effect=cli.Fail(1,'denied')):
            with self.assertRaises(cli.Fail):
                cli.run(cli.parse(['login','--google','--url','https://other.example.test','--email','other@example.test']))
        self.assertEqual(cli.default_file().read_bytes(), before)

    def test_ordinary_password_auth_does_not_change_defaults(self):
        with patch.dict(os.environ,{cli.ENV[2]:'synthetic-password'}), patch.object(cli,'send',return_value=(200,{'token':'synthetic-token'})):
            cli.login(cli.config(args=cli.parse(['whoami','--url',self.cfg['url'],'--email',self.cfg['email']])))
        self.assertFalse(cli.default_file().exists())

    def test_password_login_saves_default(self):
        with patch.dict(os.environ,{cli.ENV[2]:'synthetic-password'}), patch.object(cli,'send',return_value=(200,{'token':'synthetic-token'})), contextlib.redirect_stdout(io.StringIO()):
            cli.run(cli.parse(['login','--password','--url',self.cfg['url'],'--email',self.cfg['email']]))
        self.assertEqual(cli.config(), self.cfg)

    def test_logout_and_forget(self):
        self.login()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.run(cli.parse(['logout']))
        self.assertIsNone(cli.load_session(self.cfg))
        self.assertEqual(cli.config(), self.cfg)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.run(cli.parse(['logout','--forget']))
        self.assertFalse(cli.default_file().exists())
        with self.assertRaises(cli.Fail): cli.config()

    def test_forget_other_account_preserves_default(self):
        self.login()
        other = {**self.cfg,'email':'other@example.test'}
        cli.save_session(other,{**other,'token':'other-token'})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.run(cli.parse(['logout','--forget','--email',other['email']]))
        self.assertEqual(cli.config(), self.cfg)
        self.assertIsNone(cli.load_session(other))
        self.assertIsNotNone(cli.load_session(self.cfg))

    def test_corrupt_and_unsafe_defaults_fail_closed(self):
        self.login()
        cli.default_file().write_text('{')
        with self.assertRaises(cli.Fail): cli.config()
        cli.default_file().write_text(json.dumps({'url':self.cfg['url'],'email':self.cfg['email']}))
        cli.default_file().chmod(0o644)
        with self.assertRaises(cli.Fail): cli.config()
        cli.default_file().unlink()
        target=Path(self.tmp.name)/'target'; target.write_text('{}'); target.chmod(0o600)
        cli.default_file().symlink_to(target)
        with self.assertRaises(cli.Fail): cli.config()

    def test_failed_session_write_does_not_change_default(self):
        self.login()
        before = cli.default_file().read_bytes()
        session = {'url': 'https://other.example.test', 'email': 'other@example.test', 'token': 'new-token'}
        with patch.object(cli, 'google_login', return_value=session), patch.object(cli, 'save_session', return_value=False):
            with self.assertRaises(cli.Fail):
                cli.run(cli.parse(['login', '--google', '--url', session['url'], '--email', session['email']]))
        self.assertEqual(cli.default_file().read_bytes(), before)

    def test_refresh_does_not_change_default_account(self):
        self.login()
        before = cli.default_file().read_bytes()
        other = {**self.cfg, 'email':'other@example.test'}
        record = {'collectionName':'agents', 'id':'synthetic000001', 'email':other['email']}
        cli.auth_session(other, {'token':'renewed-token', 'record':record}, 'google')
        self.assertEqual(cli.default_file().read_bytes(), before)
        self.assertEqual(cli.load_session(other)['token'],'renewed-token')

    def test_unsafe_remote_url_is_rejected_before_login(self):
        with patch.object(cli, 'google_login') as login:
            with self.assertRaises(cli.Fail):
                cli.run(cli.parse(['login','--google','--url','http://remote.example.test','--email',self.cfg['email']]))
            login.assert_not_called()

    def test_session_directory_override(self):
        with patch.dict(os.environ, {'PEOPLECONTEXT_SESSION_DIR': str(Path(self.tmp.name)/'override')}):
            self.login()
            self.assertEqual(cli.default_file(), Path(self.tmp.name)/'override/default.json')
        with self.assertRaises(cli.Fail): cli.config()

if __name__ == '__main__': unittest.main()

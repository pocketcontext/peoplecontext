"""Guard application security configuration independently of server execution."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / 'pocketcontext.json').read_text())

    def test_explicit_output_and_policy_boundaries(self):
        config = self.config
        self.assertEqual(config['authCollection'], 'agents')
        self.assertEqual(set(config['tables']), {'employees', 'compensation', 'personal_details', 'hr_notes'})
        policy = config['snapshot']['policyTables']
        self.assertEqual(set(policy), {'account_links', 'hr_members', 'reporting_lines'})
        self.assertFalse(set(policy) & set(config['tables']))
        for name, columns in {**config['tables'], **policy}.items():
            with self.subTest(table=name):
                self.assertTrue(columns)
                self.assertNotIn('*', columns)
                self.assertFalse({'password', 'tokenKey', 'email', 'emailVisibility'} & set(columns))

    def test_every_private_export_requires_server_bound_identity(self):
        filters = self.config['snapshot']['filters']
        self.assertEqual(set(filters), set(self.config['tables']))
        self.assertEqual(filters['employees'].strip(), '1')
        for table in ('compensation', 'personal_details', 'hr_notes'):
            with self.subTest(table=table):
                self.assertIn(':requester', filters[table])
                self.assertEqual(set(re.findall(r':([A-Za-z_][A-Za-z_0-9]*)', filters[table])), {'requester'})

    def test_export_has_independent_finite_limits(self):
        snapshot = self.config['snapshot']
        for key, low, high in [('timeoutMs', 1, 30000), ('maxRows', 1, 1000000),
                               ('maxBytes', 1024, 67108864), ('maxConcurrent', 1, 16)]:
            with self.subTest(limit=key):
                self.assertIs(type(snapshot[key]), int)
                self.assertGreaterEqual(snapshot[key], low)
                self.assertLessEqual(snapshot[key], high)
        self.assertRegex((ROOT / 'POCKETCONTEXT_VERSION').read_text().strip(), r'^[0-9a-f]{40}$')


if __name__ == '__main__':
    unittest.main()

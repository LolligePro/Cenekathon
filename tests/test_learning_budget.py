import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import test_talent as fixtures
from skillmarket_staging import learning_budget as budget


class LearningBudgetTests(unittest.TestCase):
    tearDown = fixtures.TalentTests.tearDown

    def setUp(self):
        fixtures.TalentTests.setUp(self)
        rows = [
            ('base', ['S024']), ('ml1', ['S024', 'S091']), ('ml2', ['S024', 'S091']),
            ('data', ['S024', 'S007']), ('pair1', ['S024', 'S007', 'S091']),
            ('pair2', ['S024', 'S007', 'S091']), ('pair3', ['S024', 'S007', 'S091']),
            ('triple', ['S024', 'S007', 'S091', 'S125']), ('empty', []),
            ('old', ['S024', 'S007', 'S091']), ('gb', ['S024', 'S007', 'S091'])]
        with duckdb.connect(str(self.database)) as conn:
            for posting, skills in rows:
                conn.execute('''INSERT INTO skillmarket_app.postings(posting_id,parse_status,date_posted,country,job_type)
                    VALUES (?,'ok',?::DATE,?,'technology')''',
                    [posting, '2024-05-01' if posting == 'old' else '2025-05-01', 'GB' if posting == 'gb' else 'BE'])
                if skills:
                    conn.executemany('INSERT INTO skillmarket_app.posting_skills VALUES (?,?)', [(posting, s) for s in skills])
            conn.execute("INSERT INTO skillmarket_app.monthly_totals VALUES ('technology',DATE '2025-05-01',9)")

    def test_pair_counts_distinct_new_full_matches_and_synergy(self):
        result = budget.plan_learning_budget(['SQL'], 40, 'BE', 'technology')
        self.assertEqual(result['baseline_matches'], 1)
        best = result['plans'][0]
        self.assertEqual({s['skill_id'] for s in best['skills']}, {'S007', 'S091'})
        self.assertEqual((best['hours'], best['extra_matches'], best['total_matches'], best['combination_bonus']), (40, 6, 7, 3))
        self.assertEqual(len(best['courses']), 1)
        self.assertEqual(best['courses'][0]['covered_count'], 2)
        gb = budget.plan_learning_budget(['S024'], 40, 'GB', 'technology')
        self.assertEqual(gb['plans'][0]['extra_matches'], 1)

    def test_budget_custom_hours_empty_results_and_validation(self):
        result = budget.plan_learning_budget(['SQL'], 20, 'BE', 'technology')
        self.assertEqual(result['plans'][0]['extra_matches'], 2)
        self.assertEqual(len(result['plans'][0]['skills']), 1)
        custom = budget.plan_learning_budget(['SQL'], 20, 'BE', 'technology', {'ML': 30, 'S007': 10})
        self.assertEqual(custom['plans'][0]['extra_matches'], 1)
        self.assertEqual(custom['plans'][0]['hours'], 10)
        tiny = budget.plan_learning_budget(['SQL'], 5, 'BE', 'technology')
        self.assertEqual(tiny['plans'], [])
        self.assertIn('Geen pakket', budget.format_budget(tiny))
        self.assertIsNone(budget.budget_chart(tiny))
        for kwargs in [{'hours_budget': 0}, {'hours_budget': True}, {'hours_budget': float('nan')},
                       {'skill_hours': {'ML': -1}}, {'skills': ['unknown skill']}, {'job_type': 'invented'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                budget.plan_learning_budget(**({'skills': ['SQL']} | kwargs))

    def test_chat_uses_budget_tool_and_reuses_profile_for_follow_up(self):
        import ai_layer
        import server
        from fastapi.testclient import TestClient
        with (patch.object(ai_layer, '_get_client', side_effect=AssertionError('No model needed')),
              patch.object(ai_layer, 'USAGE_LOG', self.root/'usage.jsonl'),
              patch.object(server, 'sessions', {}), TestClient(server.app) as client):
            first = client.post('/api/ask', json={'question': 'Ik ken SQL en heb 40 uur. Welk skillpakket voor technology in België?'}).json()
            self.assertIn('+6 extra vacaturematches', first['answer'])
            self.assertIn('3 van die extra vacatures vereisen beide', first['answer'])
            self.assertIn('https://example.org/', first['answer'])
            self.assertIn('demo-aanname', first['answer'])
            self.assertEqual(len(first['charts']), 1)
            second = client.post('/api/ask', json={'question': 'En met 20 uur?', 'session_id': first['session_id']}).json()
            self.assertIn('+2 extra vacaturematches', second['answer'])
            self.assertEqual(second['usage']['input_tokens'], 0)
            courses = client.post('/api/ask', json={'question': 'Welke courses hiervoor?', 'session_id': first['session_id']}).json()
            self.assertIn('https://example.org/', courses['answer'])

    def test_model_selected_budget_tool_uses_same_verified_formatter(self):
        import ai_layer
        tool = SimpleNamespace(type='tool_use', id='budget', name='plan_learning_budget',
            input={'skills': ['SQL'], 'hours_budget': 40, 'country': 'BE', 'job_type': 'technology'})
        response = SimpleNamespace(content=[tool], stop_reason='tool_use', usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
        with patch.object(ai_layer, '_get_client', return_value=fake), patch.object(ai_layer, 'USAGE_LOG', self.root/'usage.jsonl'):
            result = ai_layer.ask('Maak een opleidingsplan voor mijn beschikbare tijd.')
        self.assertIn('+6 extra vacaturematches', result['answer'])
        self.assertEqual(len(result['charts']), 1)
        self.assertEqual(result['usage']['output_tokens'], 1)


if __name__ == '__main__':
    unittest.main()

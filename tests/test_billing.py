from decimal import Decimal
from threading import Event
import unittest

from subtitle_app.billing import Pricing, UsageRecord, money_text, usage_report, window_summary
from subtitle_app.subtitles import SubtitleCue
from subtitle_app.translation import TokenUsage, TranslationConfig, translate_subtitles
from test_translation import response_for


class BillingTests(unittest.TestCase):
    def test_input_and_output_rates_use_millions_and_decimal_arithmetic(self):
        pricing = Pricing('2', '8', 'CNY')
        usage = TokenUsage(1000, 500, 1)
        self.assertEqual(pricing.cost(usage), Decimal('0.006'))
        self.assertEqual(Pricing('0.1', '0.3').cost(TokenUsage(1, 1, 1)), Decimal('0.0000004'))
        self.assertEqual(money_text(Decimal('0.0000004')), '<0.000001')
        self.assertEqual(money_text(Decimal('0')), '0.000000')

    def test_no_price_differs_from_explicit_free_rate(self):
        usage = TokenUsage(1000, 500, 1)
        unpriced = UsageRecord('https://example.com/v1', 'model', None, usage)
        free = UsageRecord('http://localhost/v1', 'local', Pricing(0, 0), usage)
        self.assertIn('未设置单价', unpriced.cost_summary())
        self.assertIsNone(unpriced.to_dict()['estimated_known_cost'])
        self.assertIn('CNY 0.000000', free.cost_summary())
        self.assertEqual(free.to_dict()['estimated_known_cost'], '0')

    def test_unknown_calls_never_appear_as_known_zero_or_complete_total(self):
        record = UsageRecord('https://example.com/v1', 'model', Pricing(2, 8), TokenUsage(0, 0, 0, 1))
        self.assertIn('未知', record.cost_summary())
        self.assertIsNone(record.to_dict()['estimated_known_cost'])
        self.assertFalse(record.to_dict()['estimate_complete'])
        self.assertEqual(usage_report([record])['estimated_known_cost_by_currency'], {})
        record.usage = TokenUsage(1000, 500, 1, 1)
        self.assertIn('仅已知部分', record.cost_summary())
        report = usage_report([record])
        self.assertEqual(report['estimated_known_cost_by_currency'], {'CNY': '0.006'})
        self.assertFalse(report['estimate_complete'])

    def test_currency_totals_are_separate_and_unpriced_calls_are_visible(self):
        records = [UsageRecord('https://cn.example/v1', 'cn', Pricing(2, 8), TokenUsage(1000, 500, 1)),
                   UsageRecord('https://us.example/v1', 'us', Pricing(1, 2, 'USD'), TokenUsage(1000, 500, 1)),
                   UsageRecord('https://other.example/v1', 'unknown-price', None, TokenUsage(1000, 500, 1))]
        report = usage_report(records)
        self.assertEqual(report['estimated_known_cost_by_currency'], {'CNY': '0.006', 'USD': '0.002'})
        self.assertEqual(report['reported_total_tokens'], 4500)
        self.assertEqual(report['unpriced_calls'], 1)
        self.assertFalse(report['estimate_complete'])
        summary = window_summary(records)
        self.assertIn('CNY 0.006000', summary)
        self.assertIn('USD 0.002000', summary)
        self.assertIn('1 次未设单价', summary)

    def test_invalid_rates_and_currency_are_rejected(self):
        for value in ('-1', 'NaN', 'Infinity', 'not-a-price'):
            with self.assertRaises(ValueError):
                Pricing(value, 1)
        with self.assertRaises(ValueError):
            Pricing(1, 1, 'unknown')

    def test_network_failure_emits_unknown_usage_snapshot(self):
        events = []
        def fail(config, payload):
            raise ValueError('connection failed')
        with self.assertRaises(ValueError):
            translate_subtitles([SubtitleCue(0, 1, 'hello')], TranslationConfig('http://localhost/v1', 'm'),
                                Event(), lambda *event: events.append(event), fail)
        usage = [value for kind, value in events if kind == 'token_usage'][-1]
        self.assertEqual(usage.unreported_calls, 1)
        self.assertEqual(usage.reported_calls, 0)

    def test_usage_snapshots_do_not_mutate_or_double_count_batches(self):
        events = []
        def transport(config, payload):
            # Two separate calls each report the same billed token quantities.
            import json
            row = json.loads(payload['messages'][1]['content'])[0]
            return response_for([{'id': row['id'], 'text': '译文'}],
                                {'prompt_tokens': 20, 'completion_tokens': 10})
        translate_subtitles([SubtitleCue(0, 1, 'a'), SubtitleCue(2, 3, 'b')],
                            TranslationConfig('http://localhost/v1', 'm', batch_size=1),
                            Event(), lambda *event: events.append(event), transport)
        snapshots = [value for kind, value in events if kind == 'token_usage']
        self.assertEqual([item.prompt_tokens for item in snapshots], [20, 40])
        self.assertEqual([item.reported_calls for item in snapshots], [1, 2])
        self.assertIsNot(snapshots[0], snapshots[1])
        record = UsageRecord('http://localhost/v1', 'm', Pricing(2, 8), snapshots[-1])
        self.assertEqual(record.to_dict()['estimated_known_cost'], '0.00024')

    def test_invalid_translation_still_counts_billed_response(self):
        events = []
        def transport(config, payload):
            return response_for([], {'prompt_tokens': 1000, 'completion_tokens': 500})
        with self.assertRaises(ValueError):
            translate_subtitles([SubtitleCue(0, 1, 'hello')], TranslationConfig('http://localhost/v1', 'm'),
                                Event(), lambda *event: events.append(event), transport)
        usage = [value for kind, value in events if kind == 'token_usage'][-1]
        self.assertEqual(Pricing(2, 8).cost(usage), Decimal('0.006'))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import unittest

from subtitle_app.background_job import Cancelled
from subtitle_app.subtitles import SubtitleCue
from subtitle_app.translation import (
    TokenUsage, TranslationConfig, parse_translation_response, request_chat, translate_subtitles,
)


def response_for(rows, usage=None):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"translations": rows}, ensure_ascii=False)}}], "usage": usage}


class LocalChatServer:
    def __init__(self, status=200, malformed=False):
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, self.headers.get('Authorization'), payload))
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/must-not-follow')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                inputs = json.loads(payload['messages'][1]['content'])
                rows = [{"id": row['id'], "text": "這是測試譯文"} for row in inputs]
                result = response_for(rows, {"prompt_tokens": 20, "completion_tokens": 10})
                self.wfile.write(b'not-json' if malformed else json.dumps(result).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.cues = [SubtitleCue(2, 3, 'Hello'), SubtitleCue(5, 6, 'World')]

    def server(self, **kwargs):
        server = LocalChatServer(**kwargs)
        self.addCleanup(server.close)
        return server

    def test_real_http_request_and_simplified_output_preserve_timing(self):
        server = self.server()
        config = TranslationConfig(server.base_url, 'test-model', 'private-key', batch_size=1)
        events = []
        result = translate_subtitles(self.cues, config, Event(), lambda *event: events.append(event))
        self.assertEqual([(c.start, c.end) for c in result], [(2, 3), (5, 6)])
        self.assertEqual(result[0].text, '这是测试译文')
        self.assertEqual(self.cues[0].text, 'Hello')
        self.assertEqual(len(server.requests), 2)
        path, auth, payload = server.requests[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(auth, 'Bearer private-key')
        self.assertEqual(payload['model'], 'test-model')
        self.assertNotIn('start', payload['messages'][1]['content'])
        self.assertIn(('usage', '已报告输入 40 / 输出 20 tokens'), events)

    def test_out_of_order_responses_map_by_id_and_reject_omissions_duplicates(self):
        result = parse_translation_response(response_for([{'id': 2, 'text': '乙'}, {'id': 1, 'text': '甲'}]), [1, 2])
        self.assertEqual(result, {1: '甲', 2: '乙'})
        for rows in ([{'id': 1, 'text': 'a'}], [{'id': 1, 'text': 'a'}, {'id': 1, 'text': 'b'}],
                     [{'id': 1, 'text': 'a'}, {'id': 3, 'text': 'b'}],
                     [{'id': 1, 'text': ''}, {'id': 2, 'text': 'b'}]):
            with self.assertRaises(ValueError):
                parse_translation_response(response_for(rows), [1, 2])

    def test_cancellation_keeps_completed_inflight_batch_and_stops_next_request(self):
        cancel, events, calls = Event(), [], []
        def transport(config, payload):
            calls.append(payload)
            cancel.set()
            return response_for([{'id': 1, 'text': '完成的译文'}])
        with self.assertRaises(Cancelled):
            translate_subtitles(self.cues, TranslationConfig('http://localhost/v1', 'model', batch_size=1),
                                cancel, lambda *event: events.append(event), transport)
        self.assertEqual(len(calls), 1)
        partial = [value for kind, value in events if kind == 'translated'][-1]
        self.assertEqual(partial, [SubtitleCue(2, 3, '完成的译文')])

    def test_failed_later_batch_preserves_prior_results_and_reports_usage(self):
        events = []
        def transport(config, payload):
            inputs = json.loads(payload['messages'][1]['content'])
            if inputs[0]['id'] == 2:
                return response_for([], {'prompt_tokens': 4, 'completion_tokens': 2})
            return response_for([{'id': 1, 'text': '译文一'}])
        with self.assertRaises(ValueError):
            translate_subtitles(self.cues, TranslationConfig('http://localhost/v1', 'm', batch_size=1),
                                Event(), lambda *event: events.append(event), transport)
        self.assertEqual([v for k, v in events if k == 'translated'][-1], [SubtitleCue(2, 3, '译文一')])
        self.assertIn('1 次请求用量未知', [v for k, v in events if k == 'usage'][-1])

    def test_http_errors_do_not_leak_key_or_retry_or_follow_redirect(self):
        for status in (401, 429, 302):
            server = self.server(status=status)
            config = TranslationConfig(server.base_url, 'm', 'secret-value')
            with self.assertRaises(ValueError) as error:
                request_chat(config, {'messages': [{'content': ''}, {'content': '[]'}]})
            self.assertIn(str(status), str(error.exception))
            self.assertNotIn('secret-value', str(error.exception))
            self.assertEqual(len(server.requests), 1)

    def test_malformed_http_json_and_truncated_model_results_are_rejected(self):
        server = self.server(malformed=True)
        with self.assertRaises(ValueError):
            request_chat(TranslationConfig(server.base_url, 'm'), {'messages': [{}, {'content': '[]'}]})
        response = response_for([{'id': 1, 'text': 'partial'}])
        response['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ValueError):
            parse_translation_response(response, [1])

    def test_invalid_configuration_and_unknown_usage(self):
        for base in ('file:///tmp/test', 'https://user:password@example.com/v1', 'https://example.com/v1?key=secret'):
            with self.assertRaises(ValueError):
                TranslationConfig(base, 'm').endpoint()
        usage = TokenUsage()
        usage.add(None)
        self.assertIn('未知', usage.summary())
        usage.add({'prompt_tokens': -1, 'completion_tokens': 4})
        self.assertEqual(usage.reported_calls, 0)

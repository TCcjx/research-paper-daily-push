"""Exercise rate-limit recovery without network calls or real waits."""
import contextlib
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common


def http_error(code=429, after=None):
    headers = Message()
    if after is not None:
        headers["Retry-After"] = after
    return HTTPError("https://example.org", code, "error", headers, None)


class RetryTests(unittest.TestCase):
    def request(self, errors, *, text=False):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get_content_charset.return_value = "utf-8"
        response.read.return_value = b'{}'
        with patch.object(common, "urlopen", side_effect=[*errors, response]) as request, \
             patch.object(common.time, "sleep") as sleep, \
             patch.object(common.random, "uniform", return_value=0), \
             contextlib.redirect_stderr(io.StringIO()):
            result = (common.text_request if text else common.json_request)("https://example.org", retries=4)
        return result, request, sleep

    def test_rate_limit_recovers_and_respects_numeric_header(self):
        for text in (False, True):
            result, request, sleep = self.request([http_error(after="30")], text=text)
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(30)
            self.assertEqual(result, '{}' if text else {})

    def test_rate_limit_backoff_grows_and_is_bounded(self):
        _, request, sleep = self.request([http_error()] * 3)
        self.assertEqual(request.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [15, 30, 60])

    def test_http_date_header_is_honored(self):
        after = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=40), usegmt=True)
        _, _, sleep = self.request([http_error(after=after)])
        self.assertGreater(sleep.call_args.args[0], 38)
        self.assertLessEqual(sleep.call_args.args[0], 40)

    def test_long_server_cooldown_is_not_shortened(self):
        with patch.object(common, "urlopen", side_effect=http_error(after="3600")) as request, \
             patch.object(common.time, "sleep") as sleep, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(HTTPError):
            common.json_request("https://example.org")
        request.assert_called_once()
        sleep.assert_not_called()

    def test_client_error_is_not_retried(self):
        with patch.object(common, "urlopen", side_effect=http_error(400)) as request, self.assertRaises(HTTPError):
            common.text_request("https://example.org")
        request.assert_called_once()

    def test_network_error_recovers(self):
        _, _, sleep = self.request([URLError("timeout")])
        sleep.assert_called_once_with(3)

    def test_exhausted_retry_budget_stops(self):
        with patch.object(common, "urlopen", side_effect=http_error()) as request, \
             patch.object(common.time, "sleep"), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(HTTPError):
            common.text_request("https://example.org", retries=4)
        self.assertEqual(request.call_count, 4)

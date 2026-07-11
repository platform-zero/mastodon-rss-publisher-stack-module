import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "stack.containers/mastodon-rss-publisher/publisher.py"
SPEC = importlib.util.spec_from_file_location("publisher", MODULE_PATH)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublisherTest(unittest.TestCase):
    def test_rss_and_atom_entries_are_parsed(self):
        rss = b"<rss><channel><item><guid>one</guid><title>One</title><description>&lt;b&gt;Summary&lt;/b&gt;</description><link>https://example.test/one</link></item></channel></rss>"
        atom = b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>two</id><title>Two</title><summary>Atom summary</summary><link href='https://example.test/two'/></entry></feed>"
        self.assertEqual(publisher.entries(rss)[0]["summary"], "Summary")
        self.assertEqual(publisher.entries(atom)[0]["link"], "https://example.test/two")

    def test_status_is_sanitized_and_limited(self):
        value = publisher.status({"source": "Source"}, {"title": "Title", "summary": "<p>" + "x" * 600 + "</p>", "link": "https://example.test"})
        self.assertLessEqual(len(value), 500)
        self.assertNotIn("<p>", value)
        self.assertIn("Source: Source", value)

    def test_first_fetch_does_not_backfill_and_next_entry_posts(self):
        first = b"<rss><channel><item><guid>one</guid><title>One</title><link>https://example.test/one</link></item></channel></rss>"
        second = b"<rss><channel><item><guid>two</guid><title>Two</title><link>https://example.test/two</link></item><item><guid>one</guid><title>One</title><link>https://example.test/one</link></item></channel></rss>"

        class Response:
            headers = {}
            def __init__(self, body=b""):
                self.body = body
            def read(self):
                return self.body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            feeds = root / "feeds"
            state = root / "state"
            feeds.mkdir()
            (feeds / "test.json").write_text(json.dumps({"feeds": [{"id": "test", "account": "rss_test", "display_name": "Test", "source": "Test", "url": "https://feed.test/rss"}]}))
            state.mkdir()
            (state / "credentials.json").write_text(json.dumps({"rss_test": {"token": "test-token"}}))
            original_feeds, original_state = publisher.FEED_DIR, publisher.STATE_DIR
            publisher.FEED_DIR, publisher.STATE_DIR = feeds, state
            calls = []
            bodies = [first, second]
            def urlopen(request, timeout=0):
                if request.full_url.endswith("/api/v1/statuses"):
                    calls.append(json.loads(request.data)["status"])
                    return Response()
                return Response(bodies.pop(0))
            try:
                with patch.object(publisher.urllib.request, "urlopen", side_effect=urlopen):
                    publisher.poll_once()
                    publisher.poll_once()
            finally:
                publisher.FEED_DIR, publisher.STATE_DIR = original_feeds, original_state
            self.assertEqual(len(calls), 1)
            self.assertIn("Two", calls[0])

    def test_calibration_deduplicates_urls_and_reports_local_share(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_state = publisher.STATE_DIR
            publisher.STATE_DIR = Path(temporary)
            try:
                connection = publisher.db()
                now = int(__import__("time").time())
                connection.executemany(
                    "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [("aus", "AUS", "ABC", "https://example.test/story", "a local story", now, now), ("world", "WORLD", "BBC", "https://example.test/story", "a local story", now, now), ("world-2", "WORLD", "DW", "https://example.test/world", "world story", now, now)],
                )
                report = publisher.calibration_report(connection)
            finally:
                publisher.STATE_DIR = original_state
            self.assertEqual(report["deduplicated_candidates"], 2)
            self.assertEqual(report["regional_candidates"]["AUS"], 1)


if __name__ == "__main__":
    unittest.main()

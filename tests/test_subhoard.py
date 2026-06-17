import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import subhoard


class FakeSMTP:
    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self.quit_called = False

    def sendmail(self, from_address, to_addresses, message):
        if self.error:
            raise self.error
        self.calls.append((from_address, to_addresses, message))

    def quit(self):
        self.quit_called = True


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir_patch = patch.object(
            subhoard, "CACHE_DIR", self.temp_dir.name
        )
        self.cache_dir_patch.start()

    def tearDown(self):
        self.cache_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_cache_tracks_email_delivery(self):
        post = {
            "title": "A post",
            "subtitle": "",
            "url": "https://example.substack.com/p/a-post",
            "date": "2025-01-02",
            "slug": "a-post",
        }

        subhoard.save_to_cache(post, "Markdown", "<p>HTML</p>")
        self.assertFalse(subhoard.load_from_cache(post)["emailed"])

        subhoard.mark_cached_post_emailed(post)
        cached = subhoard.load_from_cache(post)
        self.assertTrue(cached["emailed"])
        self.assertEqual(cached["html"], "<p>HTML</p>")
        self.assertFalse(os.path.exists(f"{subhoard.cache_path(post)}.tmp"))

    def test_exports_only_requested_posts_in_chronological_order(self):
        included_newer = {
            "title": "Newer",
            "subtitle": "",
            "url": "https://example.substack.com/p/newer",
            "date": "2025-02-01",
            "slug": "newer",
        }
        included_older = {
            "title": "Older",
            "subtitle": "",
            "url": "https://example.substack.com/p/older",
            "date": "2025-01-01",
            "slug": "older",
        }
        unrelated = {
            "title": "Unrelated",
            "subtitle": "",
            "url": "https://other.substack.com/p/unrelated",
            "date": "2025-03-01",
            "slug": "unrelated",
        }
        for post in (included_newer, included_older, unrelated):
            subhoard.save_to_cache(post, post["title"])

        yearly = subhoard.load_all_cached_posts(
            [included_newer, included_older]
        )

        self.assertEqual(
            [post["title"] for post in yearly["2025"]],
            ["Older", "Newer"],
        )

    def test_invalid_json_cache_is_ignored(self):
        post = {
            "title": "Broken",
            "url": "https://example.substack.com/p/broken",
            "slug": "broken",
        }
        with open(subhoard.cache_path(post), "w", encoding="utf-8") as cache:
            cache.write("{")

        self.assertIsNone(subhoard.load_from_cache(post))


class EmailTests(unittest.TestCase):
    def test_email_uses_parsed_envelope_addresses(self):
        smtp = FakeSMTP()
        post = {
            "title": "Subject",
            "date": "2025-01-02",
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                subhoard, "FROM_ADDRESS", "Sender <sender@example.com>"
            ))
            stack.enter_context(patch.object(
                subhoard, "TO_ADDRESS", "Recipient <recipient@example.com>"
            ))
            subhoard.send_email(smtp, post, "<p>Body</p>")

        from_address, to_addresses, _ = smtp.calls[0]
        self.assertEqual(from_address, "sender@example.com")
        self.assertEqual(to_addresses, ["recipient@example.com"])

    def test_email_body_supports_unicode(self):
        smtp = FakeSMTP()
        post = {
            "title": "Café",
            "date": "2025-01-02",
        }

        subhoard.send_email(smtp, post, "<p>Résumé — naïve</p>")

        self.assertIn("Content-Transfer-Encoding: base64", smtp.calls[0][2])

    def test_email_header_fields_are_html_escaped(self):
        post = {
            "title": "A <dangerous> title",
            "subtitle": "Fish & chips",
            "url": 'https://example.com/?a=1&b="2"',
            "date": "2025-01-02",
        }

        rendered = subhoard.build_email_html(post, "<p>Trusted body</p>")

        self.assertIn("A &lt;dangerous&gt; title", rendered)
        self.assertIn("Fish &amp; chips", rendered)
        self.assertNotIn("<dangerous>", rendered)

    def test_delivery_reconnects_once(self):
        failed_smtp = FakeSMTP(error=OSError("connection lost"))
        replacement_smtp = FakeSMTP()
        post = {
            "title": "Subject",
            "date": "2025-01-02",
        }

        with patch.object(
            subhoard,
            "connect_smtp",
            return_value=replacement_smtp,
        ) as reconnect:
            result = subhoard.deliver_email(
                failed_smtp,
                post,
                "<p>Body</p>",
            )

        reconnect.assert_called_once_with()
        self.assertIs(result, replacement_smtp)
        self.assertTrue(failed_smtp.quit_called)
        self.assertEqual(len(replacement_smtp.calls), 1)


class ConfigurationTests(unittest.TestCase):
    def test_output_modes_are_deduplicated(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                subhoard, "SUBSTACK_URL", "https://example.com"
            ))
            stack.enter_context(patch.object(
                subhoard, "OUTPUT_MODE", ["digest", "digest"]
            ))
            stack.enter_context(patch.object(
                subhoard, "START_DATE", None
            ))
            stack.enter_context(patch.object(
                subhoard, "FETCH_DELAY", 0
            ))
            stack.enter_context(patch.object(
                subhoard, "EMAIL_DELAY", 0
            ))
            subhoard.validate_config()
            self.assertEqual(subhoard.OUTPUT_MODE, ["digest"])


if __name__ == "__main__":
    unittest.main()

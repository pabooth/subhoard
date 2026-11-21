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

    def test_legacy_cache_files_remain_readable(self):
        post = {
            "title": "Legacy",
            "url": "https://example.substack.com/p/legacy",
            "slug": "legacy",
        }
        with open(
            subhoard.legacy_cache_path(post),
            "w",
            encoding="utf-8",
        ) as cache:
            cache.write('{"title": "Legacy", "markdown": "Cached"}')

        self.assertEqual(subhoard.load_from_cache(post)["markdown"], "Cached")

    def test_cache_files_are_private(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions only")
        post = {
            "title": "Private",
            "url": "https://example.substack.com/p/private",
            "slug": "private",
        }

        subhoard.save_to_cache(post, "Sensitive content")

        self.assertEqual(
            os.stat(subhoard.cache_path(post)).st_mode & 0o777,
            0o600,
        )


class CookieTests(unittest.TestCase):
    def test_http_only_cookies_are_loaded(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as cookie_file:
            cookie_file.write(
                "#HttpOnly_.substack.com\tTRUE\t/\tTRUE\t0\t"
                "substack.sid\tsecret\n"
            )
            cookie_file.flush()

            cookies = subhoard.load_cookies(cookie_file.name)

        self.assertEqual(cookies[0]["domain"], ".substack.com")
        self.assertEqual(cookies[0]["name"], "substack.sid")


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

    def test_email_subject_cannot_inject_headers(self):
        smtp = FakeSMTP()
        post = {
            "title": "Subject\r\nBcc: victim@example.com",
            "date": "2025-01-02",
        }

        subhoard.send_email(smtp, post, "<p>Body</p>")

        message = smtp.calls[0][2]
        self.assertIn("Subject: Subject Bcc: victim@example.com", message)
        self.assertNotIn("\nBcc: victim@example.com", message)


class ContentSecurityTests(unittest.TestCase):
    @unittest.skipIf(
        subhoard.BeautifulSoup is None,
        "beautifulsoup4 is not installed",
    )
    def test_active_html_is_removed(self):
        content = subhoard.BeautifulSoup(
            """
            <article>
              <script>alert(1)</script>
              <a href="javascript:alert(2)" onclick="alert(3)">Link</a>
              <img src="https://example.com/image.png" onerror="alert(4)">
            </article>
            """,
            "html.parser",
        )

        subhoard.sanitise_content(content)

        rendered = str(content)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("onerror", rendered)
        self.assertIn("https://example.com/image.png", rendered)


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

    def test_cli_defaults_to_digest_and_reads_environment(self):
        with patch.dict(
            os.environ,
            {
                "SUBHOARD_URL": "https://example.substack.com",
                "SUBHOARD_OUTPUT": "digest,pdf",
            },
            clear=False,
        ):
            parser = subhoard.build_parser()
            args = parser.parse_args([])
            subhoard.configure_from_args(args, parser)

        self.assertEqual(subhoard.SUBSTACK_URL, "https://example.substack.com")
        self.assertEqual(subhoard.OUTPUT_MODE, ["digest", "pdf"])

    def test_post_navigation_is_limited_to_publication_host(self):
        with patch.object(
            subhoard,
            "SUBSTACK_URL",
            "https://example.substack.com",
        ):
            self.assertTrue(
                subhoard.is_safe_post_url(
                    "https://example.substack.com/p/allowed"
                )
            )
            self.assertFalse(
                subhoard.is_safe_post_url("https://attacker.example/p/no")
            )


class FakeArchivePage:
    """Stands in for a Camoufox page serving archive API responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def goto(self, url, **kwargs):
        self.urls.append(url)

    def evaluate(self, script):
        return self.responses.pop(0)


def archive_post(slug, date, audience="everyone"):
    return {
        "title": slug.title(),
        "subtitle": "",
        "slug": slug,
        "post_date": f"{date}T08:00:00.000Z",
        "audience": audience,
        "canonical_url": f"https://example.substack.com/p/{slug}",
    }


class ArchiveTests(unittest.TestCase):
    def fetch(self, responses, start_date=None):
        page = FakeArchivePage(responses)
        with ExitStack() as stack:
            for name, value in {
                "SUBSTACK_URL": "https://example.substack.com",
                "FREE_ONLY": True,
                "START_DATE": start_date,
                "FETCH_DELAY": 0,
            }.items():
                stack.enter_context(patch.object(subhoard, name, value))
            posts = subhoard.get_archive_posts(page)
        return posts, page.urls

    def test_follows_next_cursor_until_exhausted(self):
        posts, urls = self.fetch([
            {"posts": [archive_post("two", "2026-02-01")], "nextCursor": "c/1="},
            {"posts": [archive_post("one", "2026-01-01")], "nextCursor": None},
        ])

        self.assertEqual([p["slug"] for p in posts], ["two", "one"])
        self.assertEqual(len(urls), 2)
        self.assertNotIn("offset", urls[0])
        self.assertIn("limit=25", urls[0])
        self.assertNotIn("cursor", urls[0])
        self.assertTrue(urls[1].endswith("&cursor=c%2F1%3D"))

    def test_skips_paid_posts_in_free_only_mode(self):
        posts, _ = self.fetch([
            {
                "posts": [
                    archive_post("free", "2026-02-01"),
                    archive_post("paid", "2026-01-01", audience="only_paid"),
                ],
                "nextCursor": None,
            },
        ])

        self.assertEqual([p["slug"] for p in posts], ["free"])

    def test_stops_at_start_date(self):
        posts, urls = self.fetch(
            [
                {
                    "posts": [
                        archive_post("new", "2026-02-01"),
                        archive_post("old", "2025-12-01"),
                    ],
                    "nextCursor": "more",
                },
            ],
            start_date="2026-01-01",
        )

        self.assertEqual([p["slug"] for p in posts], ["new"])
        self.assertEqual(len(urls), 1)

    def test_api_error_is_reported_not_treated_as_empty_list(self):
        error = {"errors": [{"param": "offset", "msg": "Invalid value"}]}
        with patch("builtins.print") as fake_print:
            posts, _ = self.fetch([error])

        self.assertEqual(posts, [])
        printed = " ".join(str(c.args[0]) for c in fake_print.call_args_list)
        self.assertIn("Archive API returned an error", printed)

    def test_accepts_legacy_list_response(self):
        posts, urls = self.fetch([[archive_post("legacy", "2026-01-01")]])

        self.assertEqual([p["slug"] for p in posts], ["legacy"])
        self.assertEqual(len(urls), 1)

    def test_legacy_list_response_pages_by_offset(self):
        first = [archive_post(f"a{i}", "2026-02-01") for i in range(25)]
        second = [archive_post("b0", "2026-01-01")]
        posts, urls = self.fetch([first, second])

        self.assertEqual(len(posts), 26)
        self.assertEqual(posts[-1]["slug"], "b0")
        self.assertEqual(len(urls), 2)
        self.assertNotIn("offset", urls[0])
        self.assertTrue(urls[1].endswith("&offset=25"))
        self.assertNotIn("cursor", urls[1])

    def test_repeating_cursor_cycle_stops_with_error(self):
        responses = [
            {"posts": [archive_post("p0", "2026-04-01")], "nextCursor": "A"},
            {"posts": [archive_post("p1", "2026-03-01")], "nextCursor": "B"},
            {"posts": [archive_post("p2", "2026-02-01")], "nextCursor": "A"},
            {"posts": [archive_post("p3", "2026-01-01")], "nextCursor": "B"},
        ]
        with patch("builtins.print") as fake_print:
            posts, urls = self.fetch(responses)

        self.assertEqual([p["slug"] for p in posts], ["p0", "p1", "p2"])
        self.assertEqual(len(urls), 3)
        printed = " ".join(str(c.args[0]) for c in fake_print.call_args_list)
        self.assertIn("pagination is not advancing", printed)


class PublicationNameTests(unittest.TestCase):
    def name_for(self, url):
        with patch.object(subhoard, "SUBSTACK_URL", url):
            return subhoard.publication_name()

    def test_substack_subdomain(self):
        self.assertEqual(self.name_for("https://example.substack.com"), "Example")

    def test_custom_domain_ignores_www(self):
        self.assertEqual(
            self.name_for("https://www.astralcodexten.com/"), "Astralcodexten"
        )

    def test_custom_domain_without_www(self):
        self.assertEqual(self.name_for("https://slowboring.com"), "Slowboring")

    def test_hostname_is_case_insensitive(self):
        self.assertEqual(self.name_for("https://WWW.Example.com"), "Example")

    def test_digest_filename_uses_publication_name(self):
        post = {
            "title": "A post",
            "subtitle": "",
            "url": "https://www.example.com/p/a-post",
            "date": "2026-01-01",
            "body_md": "Body",
        }
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(
                patch.object(subhoard, "SUBSTACK_URL", "https://www.example.com")
            )
            stack.enter_context(
                patch.object(subhoard, "DIGEST_OUTPUT_DIR", temp_dir)
            )
            stack.enter_context(patch("builtins.print"))
            subhoard.write_digest_files({"2026": [post]})
            self.assertEqual(os.listdir(temp_dir), ["Example_2026.md"])


def reportlab_available():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(reportlab_available(), "reportlab is not installed")
class PdfExportTests(unittest.TestCase):
    def test_writes_one_pdf_per_year_with_escaped_content(self):
        markdown = (
            "# Heading\n\n## Sub\n\nBody with <tags> & ampersands.\n\n"
            "> A quote\n\n- item one\n- item two\n\nCafé “quotes”."
        )
        post = {
            "title": "First & <Best>",
            "subtitle": "A subtitle",
            "url": "https://example.substack.com/p/first",
            "date": "2026-01-02",
            "markdown": markdown,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with ExitStack() as stack:
                stack.enter_context(patch.object(subhoard, "PDF_OUTPUT_DIR", temp_dir))
                stack.enter_context(
                    patch.object(subhoard, "SUBSTACK_URL", "https://example.substack.com")
                )
                stack.enter_context(patch("builtins.print"))
                older = dict(post, date="2025-06-01")
                subhoard.write_pdf_files({"2026": [post], "2025": [older]})

            self.assertEqual(
                sorted(os.listdir(temp_dir)), ["Example_2025.pdf", "Example_2026.pdf"]
            )
            with open(os.path.join(temp_dir, "Example_2026.pdf"), "rb") as f:
                self.assertEqual(f.read(5), b"%PDF-")


if __name__ == "__main__":
    unittest.main()

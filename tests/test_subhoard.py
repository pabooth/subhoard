import io
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

    def test_group_by_year_without_cache(self):
        posts = [
            {"title": "B", "date": "2025-02-01"},
            {"title": "A", "date": "2025-01-01"},
            {"title": "Old", "date": "2024-12-31"},
            {"title": "Undated", "date": ""},
        ]
        yearly = subhoard.group_by_year(posts)
        self.assertEqual([p["title"] for p in yearly["2025"]], ["A", "B"])
        self.assertEqual([p["title"] for p in yearly["2024"]], ["Old"])
        self.assertEqual([p["title"] for p in yearly["unknown"]], ["Undated"])

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
                subhoard, "OUTPUT_MODE", ["digest", "markdown", "digest"]
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
            self.assertEqual(subhoard.OUTPUT_MODE, ["markdown"])

    def test_cli_defaults_to_markdown_and_reads_environment(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SUBHOARD_")}
        env.update({
            "SUBHOARD_URL": "https://example.substack.com",
            "SUBHOARD_PDF_OUTPUT": "true",
        })
        with patch.dict(os.environ, env, clear=True):
            parser = subhoard.build_parser()
            args = parser.parse_args([])
            subhoard.configure_from_args(args, parser)

        self.assertEqual(subhoard.SUBSTACK_URL, "https://example.substack.com")
        self.assertEqual(subhoard.OUTPUT_MODE, ["markdown", "pdf"])

    def write_config(self, text):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".shconfig", delete=False, encoding="utf-8"
        ) as config:
            config.write(text)
        self.addCleanup(os.unlink, config.name)
        return config.name

    def test_config_file_supplies_defaults_below_env_and_cli(self):
        path = self.write_config(
            "# comment\n"
            "SUBHOARD_URL=https://file.substack.com\n"
            "export pdf_output = 'true'\n"
            'cache_dir="file_cache"\n'
            "FETCH_DELAY=2.5\n"
        )
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SUBHOARD_")}
        env["SUBHOARD_CACHE_DIR"] = "env_cache"
        with patch.dict(os.environ, env, clear=True):
            argv = ["--config", path, "--fetch-delay", "3"]
            self.assertEqual(subhoard.load_config(argv), path)
            parser = subhoard.build_parser()
            args = parser.parse_args(argv)
            subhoard.configure_from_args(args, parser)

        self.assertEqual(subhoard.SUBSTACK_URL, "https://file.substack.com")
        self.assertEqual(subhoard.OUTPUT_MODE, ["markdown", "pdf"])
        self.assertEqual(subhoard.CACHE_DIR, "env_cache")
        self.assertEqual(subhoard.FETCH_DELAY, 3.0)

    def test_config_file_path_from_environment(self):
        path = self.write_config("URL=https://env-config.substack.com\n")
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        with patch.dict(os.environ, {"SUBHOARD_CONFIG": path}):
            self.assertEqual(subhoard.load_config([]), path)
        self.assertEqual(
            subhoard.FILE_CONFIG, {"URL": "https://env-config.substack.com"}
        )

    def test_missing_default_config_is_ignored(self):
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        with tempfile.TemporaryDirectory() as home, patch.dict(
            os.environ, {"HOME": home}
        ):
            os.environ.pop("SUBHOARD_CONFIG", None)
            self.assertIsNone(subhoard.load_config([]))
        self.assertEqual(subhoard.FILE_CONFIG, {})

    def test_default_config_is_read_from_home(self):
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        with tempfile.TemporaryDirectory() as home, patch.dict(
            os.environ, {"HOME": home}
        ):
            os.environ.pop("SUBHOARD_CONFIG", None)
            path = os.path.join(home, ".shconfig")
            with open(path, "w", encoding="utf-8") as config:
                config.write("URL=https://home.substack.com\n")
            self.assertEqual(subhoard.load_config([]), path)
        self.assertEqual(subhoard.FILE_CONFIG["URL"], "https://home.substack.com")

    def configure_with(self, config_text, argv=(), environ=None):
        """Load a config file and apply it as main() would."""
        path = self.write_config(config_text)
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SUBHOARD_")}
        env.update(environ or {})
        argv = ["--config", path, *argv]
        with patch.dict(os.environ, env, clear=True):
            subhoard.load_config(argv)
            parser = subhoard.build_parser()
            args = parser.parse_args(argv)
            subhoard.configure_from_args(args, parser)
        return path

    def test_smtp_port_flag_overrides_invalid_config_value(self):
        self.configure_with("SMTP_PORT=invalid\n", ["--smtp-port", "465"])
        self.assertEqual(subhoard.SMTP_PORT, 465)

    def test_invalid_config_numbers_are_usage_errors(self):
        for key in ("SMTP_PORT", "FETCH_DELAY", "EMAIL_DELAY"):
            with self.subTest(key=key), patch("sys.stderr") as stderr:
                with self.assertRaises(SystemExit) as raised:
                    self.configure_with(f"{key}=invalid\n")
                self.assertEqual(raised.exception.code, 2)
                output = "".join(c.args[0] for c in stderr.write.call_args_list)
                self.assertIn("invalid", output)

    def test_config_numbers_are_converted(self):
        self.configure_with(
            "SMTP_PORT=2525\nFETCH_DELAY=2\nEMAIL_DELAY=0.25\n"
        )
        self.assertEqual(subhoard.SMTP_PORT, 2525)
        self.assertEqual(subhoard.FETCH_DELAY, 2.0)
        self.assertEqual(subhoard.EMAIL_DELAY, 0.25)

    def test_free_only_from_config_file(self):
        self.configure_with("")
        self.assertTrue(subhoard.FREE_ONLY)
        for value, expected in (("false", False), ("No", False),
                                ("0", False), ("on", True)):
            with self.subTest(value=value):
                self.configure_with(f"FREE_ONLY={value}\n")
                self.assertEqual(subhoard.FREE_ONLY, expected)

    def test_free_only_flags_override_config_file(self):
        self.configure_with("FREE_ONLY=false\n", ["--free-only"])
        self.assertTrue(subhoard.FREE_ONLY)
        self.configure_with("FREE_ONLY=true\n", ["--subscriber-content"])
        self.assertFalse(subhoard.FREE_ONLY)

    def test_invalid_free_only_is_a_usage_error(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as raised:
            self.configure_with("FREE_ONLY=maybe\n")
        self.assertEqual(raised.exception.code, 2)

    def configure_outputs(self, config_text, argv=(), environ=None):
        """Configure as a dry run, returning (outputs, stderr text)."""
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.configure_with(config_text, ["--dry-run", *argv], environ)
        return subhoard.OUTPUT_MODE, stderr.getvalue()

    def assert_usage_error(self, config_text, argv=(), environ=None):
        with (
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as raised,
        ):
            self.configure_with(config_text, argv, environ)
        self.assertEqual(raised.exception.code, 2)
        return stderr.getvalue()

    def test_outputs_default_to_markdown_only(self):
        self.assertEqual(self.configure_outputs("")[0], ["markdown"])

    def test_outputs_are_separate_settings(self):
        outputs, warnings = self.configure_outputs(
            "MARKDOWN_OUTPUT=false\nPDF_OUTPUT=yes\nEMAIL_OUTPUT=0\n"
        )
        self.assertEqual(outputs, ["pdf"])
        self.assertEqual(warnings, "")

    def test_output_flags_override_only_their_own_setting(self):
        outputs, _ = self.configure_outputs(
            "PDF_OUTPUT=true\n", ["--email", "--no-markdown"]
        )
        self.assertEqual(outputs, ["pdf", "email"])

    def test_html_output_is_a_separate_setting(self):
        outputs, _ = self.configure_outputs(
            "HTML_OUTPUT=true\n", ["--no-markdown"]
        )
        self.assertEqual(outputs, ["html"])

    def test_output_environment_overrides_config_file(self):
        outputs, _ = self.configure_outputs(
            "PDF_OUTPUT=true\n", environ={"SUBHOARD_PDF_OUTPUT": "false"}
        )
        self.assertEqual(outputs, ["markdown"])

    def test_all_outputs_off_leaves_none(self):
        outputs, _ = self.configure_outputs("MARKDOWN_OUTPUT=false\n")
        self.assertEqual(outputs, [])

    def test_empty_output_setting_keeps_default(self):
        outputs, _ = self.configure_outputs("MARKDOWN_OUTPUT=\nPDF_OUTPUT=\n")
        self.assertEqual(outputs, ["markdown"])

    def test_invalid_output_setting_is_a_usage_error(self):
        message = self.assert_usage_error("PDF_OUTPUT=maybe\n")
        self.assertIn("SUBHOARD_PDF_OUTPUT must be true or false", message)

    def test_deprecated_output_list_sets_every_output_and_warns(self):
        outputs, warnings = self.configure_outputs("OUTPUT=digest,pdf\n")
        self.assertEqual(outputs, ["markdown", "pdf"])
        self.assertIn("OUTPUT is deprecated", warnings)
        self.assertIn("MARKDOWN_OUTPUT", warnings)

        outputs, warnings = self.configure_outputs(
            "", ["--output", "pdf", "--output", "digest"]
        )
        self.assertEqual(outputs, ["markdown", "pdf"])
        self.assertIn("--output is deprecated", warnings)

    def test_deprecated_output_list_turns_off_unlisted_outputs(self):
        outputs, _ = self.configure_outputs(
            "PDF_OUTPUT=true\n", environ={"SUBHOARD_OUTPUT": "email"}
        )
        self.assertEqual(outputs, ["email"])

    def test_separate_flags_apply_over_deprecated_config_list(self):
        outputs, _ = self.configure_outputs(
            "OUTPUT=pdf\n", ["--markdown", "--no-pdf"]
        )
        self.assertEqual(outputs, ["markdown"])

    def test_one_source_cannot_mix_output_styles(self):
        message = self.assert_usage_error("OUTPUT=pdf\nPDF_OUTPUT=true\n")
        self.assertIn("not both", message)
        message = self.assert_usage_error(
            "", ["--dry-run", "--output", "pdf", "--email"]
        )
        self.assertIn("not both", message)

    def test_deprecated_output_list_rejects_unknown_values(self):
        message = self.assert_usage_error(
            "", environ={"SUBHOARD_OUTPUT": "digest,bogus"}
        )
        self.assertIn("SUBHOARD_OUTPUT contains invalid values: bogus", message)

    def test_markdown_dir_setting_and_deprecated_digest_dir(self):
        self.configure_outputs("")
        self.assertEqual(subhoard.MARKDOWN_OUTPUT_DIR, "digest_export")

        self.configure_outputs("MARKDOWN_DIR=md\n")
        self.assertEqual(subhoard.MARKDOWN_OUTPUT_DIR, "md")

        _, warnings = self.configure_outputs("DIGEST_DIR=old\n")
        self.assertEqual(subhoard.MARKDOWN_OUTPUT_DIR, "old")
        self.assertIn("DIGEST_DIR is deprecated; use MARKDOWN_DIR", warnings)

        _, warnings = self.configure_outputs(
            "MARKDOWN_DIR=md\n", ["--digest-dir", "cli"]
        )
        self.assertEqual(subhoard.MARKDOWN_OUTPUT_DIR, "cli")
        self.assertIn("--digest-dir is deprecated", warnings)

        message = self.assert_usage_error("MARKDOWN_DIR=md\nDIGEST_DIR=old\n")
        self.assertIn("use MARKDOWN_DIR or DIGEST_DIR, not both", message)

    def validate(self, **overrides):
        settings = {
            "SUBSTACK_URL": "https://example.com",
            "OUTPUT_MODE": ["markdown"],
            "CACHE_DIR": "post_cache",
            "DRY_RUN": False,
            "START_DATE": None,
            "FETCH_DELAY": 0,
            "EMAIL_DELAY": 0,
            **overrides,
        }
        with ExitStack() as stack:
            for name, value in settings.items():
                stack.enter_context(patch.object(subhoard, name, value))
            subhoard.validate_config()

    def test_no_outputs_is_valid_cache_only_run(self):
        self.validate(OUTPUT_MODE=[])

    def test_no_outputs_without_cache_is_an_error(self):
        with patch("builtins.print") as printed, self.assertRaises(SystemExit):
            self.validate(OUTPUT_MODE=[], CACHE_DIR=None)
        output = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("nothing to do", output)
        self.validate(OUTPUT_MODE=[], CACHE_DIR=None, DRY_RUN=True)

    def test_smtp_password_from_config_file(self):
        self.configure_with("SMTP_PASSWORD=file-secret\n")
        self.assertEqual(subhoard.SMTP_PASSWORD, "file-secret")

    def test_smtp_password_environment_overrides_config_file(self):
        self.configure_with(
            "SMTP_PASSWORD=file-secret\n",
            environ={"SUBHOARD_SMTP_PASSWORD": "env-secret"},
        )
        self.assertEqual(subhoard.SMTP_PASSWORD, "env-secret")

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_config_with_password_warns_only_when_exposed(self):
        path = self.write_config("SMTP_PASSWORD=file-secret\n")
        self.addCleanup(setattr, subhoard, "FILE_CONFIG", {})
        for mode, expect_warning in ((0o600, False), (0o644, True)):
            with self.subTest(mode=oct(mode)):
                os.chmod(path, mode)
                with patch("builtins.print") as printed:
                    subhoard.load_config(["--config", path])
                output = " ".join(
                    str(arg) for c in printed.call_args_list for arg in c.args
                )
                if expect_warning:
                    self.assertIn("WARNING", output)
                    self.assertIn("644", output)
                else:
                    printed.assert_not_called()

    def test_malformed_key_is_not_echoed(self):
        path = self.write_config("SMTP_PASSWORD secret-token==\n")
        with self.assertRaises(ValueError) as raised:
            subhoard.parse_config_file(path)
        message = str(raised.exception).lower()
        self.assertIn("expected key=value", message)
        self.assertNotIn("secret", message)

    def test_missing_explicit_config_is_an_error(self):
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            subhoard.load_config(["--config", "/nonexistent/subhoard.conf"])

    def test_config_file_rejects_unknown_and_malformed_lines(self):
        for text in ("URLL=https://x\n", "just words\n"):
            path = self.write_config(text)
            with self.assertRaises(ValueError):
                subhoard.parse_config_file(path)

    def test_installer_config_template_lists_every_setting(self):
        install_sh = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "install.sh",
        )
        with open(install_sh, encoding="utf-8") as script:
            source = script.read()
        start = source.index('template = """\\\n') + len('template = """\\\n')
        template = source[start:source.index('"""', start)]
        settings = subhoard.parse_config_file(self.write_config(template))
        self.assertEqual(set(settings), subhoard.CONFIG_KEYS)

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

    def test_markdown_filename_uses_publication_name(self):
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
                patch.object(subhoard, "MARKDOWN_OUTPUT_DIR", temp_dir)
            )
            stack.enter_context(patch("builtins.print"))
            subhoard.write_markdown_files({"2026": [post]})
            self.assertEqual(os.listdir(temp_dir), ["Example_2026.md"])


class HtmlExportTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = temp_dir.name
        for patcher in (
            patch.object(subhoard, "HTML_OUTPUT_DIR", self.dir),
            patch.object(subhoard, "SUBSTACK_URL", "https://example.substack.com"),
            patch("builtins.print"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return f.read()

    def test_writes_one_html_file_per_year(self):
        post = {
            "title": "First & <Best>",
            "subtitle": "A <subtitle>",
            "url": "https://example.substack.com/p/first",
            "date": "2026-01-02",
            "html": "<p>Original <strong>HTML</strong></p>",
            "markdown": "Original **HTML**",
        }
        older = dict(post, title="Older", date="2025-06-01")
        subhoard.write_html_files({"2026": [post], "2025": [older]})

        self.assertEqual(
            sorted(os.listdir(self.dir)), ["Example_2025.html", "Example_2026.html"]
        )
        page = self.read("Example_2026.html")
        self.assertIn("<title>Example — 2026</title>", page)
        self.assertIn("1 posts", page)
        self.assertIn("<h2>First &amp; &lt;Best&gt;</h2>", page)
        self.assertIn("A &lt;subtitle&gt;", page)
        self.assertIn("<p>Original <strong>HTML</strong></p>", page)
        self.assertNotIn("Older", page)

    def test_falls_back_to_markdown_without_cached_html(self):
        post = {
            "title": "Legacy",
            "subtitle": "",
            "url": "https://example.substack.com/p/legacy",
            "date": "2024-01-01",
            "html": None,
            "markdown": "Some <text> & more",
        }
        empty = dict(post, title="Empty", markdown=None)
        subhoard.write_html_files({"2024": [post, empty]})
        page = self.read("Example_2024.html")
        self.assertIn("<pre>Some &lt;text&gt; &amp; more</pre>", page)
        self.assertIn("Content unavailable", page)


class PerPostExportTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = temp_dir.name
        for patcher in (
            patch.object(subhoard, "PER_POST", True),
            patch.object(subhoard, "MARKDOWN_OUTPUT_DIR", self.dir),
            patch.object(subhoard, "HTML_OUTPUT_DIR", self.dir),
            patch.object(subhoard, "SUBSTACK_URL", "https://example.substack.com"),
            patch("builtins.print"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.posts = {
            "2024": [{
                "title": "First",
                "subtitle": "Sub",
                "url": "https://example.substack.com/p/first-post",
                "slug": "first-post",
                "date": "2024-03-15",
                "markdown": "First body",
                "html": "<p>First body</p>",
            }],
            "2025": [{
                "title": "Odd / name",
                "subtitle": "",
                "url": "https://example.substack.com/p/../../escape",
                "date": "2025-01-02",
                "markdown": "Second body",
                "html": None,
            }],
        }

    def files(self):
        return sorted(
            os.path.relpath(os.path.join(root, name), self.dir)
            for root, _, names in os.walk(self.dir)
            for name in names
        )

    def test_markdown_writes_one_file_per_post_in_year_folders(self):
        subhoard.write_markdown_files(self.posts)
        self.assertEqual(self.files(), [
            os.path.join("2024", "2024-03-15-first-post.md"),
            os.path.join("2025", "2025-01-02-escape.md"),
        ])
        path = os.path.join(self.dir, "2024", "2024-03-15-first-post.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("# First\n\n*Sub*"))
        self.assertIn("First body", text)

    def test_html_writes_one_page_per_post(self):
        subhoard.write_html_files(self.posts)
        path = os.path.join(self.dir, "2024", "2024-03-15-first-post.html")
        with open(path, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("<title>First</title>", page)
        self.assertIn("<h1>First</h1>", page)
        self.assertIn("<p>First body</p>", page)

    def test_post_filenames_are_safe(self):
        post = {"title": "../../etc/passwd", "date": "../2024-01-01", "url": ""}
        name = subhoard.post_filename(post, "md")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertEqual(subhoard.safe_filename("../", "unknown"), "unknown")

    def test_per_post_setting(self):
        config = {"PER_POST": "true"}
        with patch.dict(subhoard.FILE_CONFIG, config, clear=True):
            parser = subhoard.build_parser()
            subhoard.configure_from_args(parser.parse_args(["--dry-run"]), parser)
            self.assertTrue(subhoard.PER_POST)
            subhoard.configure_from_args(
                parser.parse_args(["--dry-run", "--no-per-post"]), parser
            )
            self.assertFalse(subhoard.PER_POST)


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

    def test_writes_one_pdf_per_post_with_per_post(self):
        post = {
            "title": "Only",
            "subtitle": "",
            "url": "https://example.substack.com/p/only",
            "slug": "only",
            "date": "2026-01-02",
            "markdown": "Body",
        }
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(patch.object(subhoard, "PDF_OUTPUT_DIR", temp_dir))
            stack.enter_context(patch.object(subhoard, "PER_POST", True))
            stack.enter_context(
                patch.object(subhoard, "SUBSTACK_URL", "https://example.substack.com")
            )
            stack.enter_context(patch("builtins.print"))
            subhoard.write_pdf_files({"2026": [post]})
            path = os.path.join(temp_dir, "2026", "2026-01-02-only.pdf")
            with open(path, "rb") as f:
                self.assertEqual(f.read(5), b"%PDF-")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kpaper  # noqa: E402

PAPER_HTML = (
    '<html><body><article class="ltx_document">'
    + "".join(f'<p class="ltx_p">Paragraph {i}</p>' for i in range(4))
    + "</article></body></html>"
)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class ParseArxivReferenceTests(unittest.TestCase):
    def test_accepts_every_link_shape_for_the_same_paper(self) -> None:
        for value in (
            "https://arxiv.org/abs/1706.03762",
            "https://arxiv.org/pdf/1706.03762",
            "https://arxiv.org/pdf/1706.03762.pdf",
            "https://arxiv.org/html/1706.03762",
            "https://ar5iv.labs.arxiv.org/html/1706.03762",
            "https://ar5iv.org/abs/1706.03762",
            "  arXiv:1706.03762  ",
            "1706.03762",
        ):
            with self.subTest(value=value):
                ref = kpaper.parse_arxiv_reference(value)
                self.assertEqual(ref, kpaper.ArxivRef("1706.03762", ""))
                self.assertEqual(ref.paper_id, "arxiv-1706-03762")

    def test_keeps_version_in_requests_but_not_in_paper_id(self) -> None:
        ref = kpaper.parse_arxiv_reference("https://arxiv.org/html/1706.03762v7")
        self.assertEqual(ref.versioned, "1706.03762v7")
        self.assertEqual(ref.paper_id, "arxiv-1706-03762")
        ref = kpaper.parse_arxiv_reference("https://arxiv.org/pdf/1706.03762v7.pdf")
        self.assertEqual(ref.versioned, "1706.03762v7")

    def test_accepts_old_style_ids(self) -> None:
        ref = kpaper.parse_arxiv_reference("https://arxiv.org/abs/hep-th/9901001v2")
        self.assertEqual(ref, kpaper.ArxivRef("hep-th/9901001", "v2"))
        self.assertEqual(ref.paper_id, "arxiv-hep-th-9901001")

    def test_rejects_non_arxiv_input(self) -> None:
        for value in (
            "https://example.com/abs/1706.03762",
            "https://arxiv.org/list/cs.CL/recent",
            "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf",
            "not a link",
            "",
        ):
            with self.subTest(value=value):
                self.assertIsNone(kpaper.parse_arxiv_reference(value))


class PaperHtmlValidationTests(unittest.TestCase):
    def test_accepts_converted_paper(self) -> None:
        self.assertTrue(kpaper.looks_like_paper_html(PAPER_HTML))

    def test_rejects_error_and_placeholder_pages(self) -> None:
        self.assertFalse(kpaper.looks_like_paper_html("<html><body>Not found</body></html>"))
        self.assertFalse(
            kpaper.looks_like_paper_html('<article class="ltx_document"><p class="ltx_p">only one</p></article>')
        )

    def test_only_non_empty_paragraphs_inside_the_article_count(self) -> None:
        empty = '<article class="ltx_document">' + '<p class="ltx_p"></p>' * 5 + "</article>"
        outside = (
            '<article class="ltx_document"><p class="ltx_p">one</p></article>'
            + '<p class="ltx_p">two</p>' * 5
        )
        two = '<article class="ltx_document"><p class="ltx_p">abstract</p><p class="ltx_p">body</p></article>'
        self.assertFalse(kpaper.looks_like_paper_html(empty))
        self.assertFalse(kpaper.looks_like_paper_html(outside))
        self.assertTrue(kpaper.looks_like_paper_html(two))

    def test_fetch_reports_why_a_candidate_was_skipped(self) -> None:
        with mock.patch.object(kpaper.requests, "get", return_value=FakeResponse(404)):
            self.assertEqual(kpaper.try_fetch_paper_html("https://x"), (None, "HTTP 404"))
        with mock.patch.object(kpaper.requests, "get", side_effect=requests.ConnectionError()):
            html, reason = kpaper.try_fetch_paper_html("https://x")
        self.assertIsNone(html)
        self.assertIn("ConnectionError", reason)


class ImportCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous)

    def run_import(self, *argv: str) -> dict:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            kpaper.main(["import", *argv, "--json"])
        return json.loads(stdout.getvalue())

    def fake_get(self, responses: dict[str, FakeResponse]):
        calls: list[str] = []

        def get(url: str, **_kwargs):
            calls.append(url)
            return responses.get(url, FakeResponse(404))

        return get, calls

    def test_abs_link_uses_official_html_first(self) -> None:
        get, calls = self.fake_get({"https://arxiv.org/html/1706.03762": FakeResponse(200, PAPER_HTML)})
        with mock.patch.object(kpaper.requests, "get", side_effect=get):
            payload = self.run_import("https://arxiv.org/abs/1706.03762")

        self.assertEqual(payload["route"], "arxiv-html")
        self.assertEqual(payload["paper_id"], "arxiv-1706-03762")
        self.assertEqual(calls, ["https://arxiv.org/html/1706.03762"])
        self.assertEqual(Path("inputs/arxiv-1706-03762.source.html").read_text(encoding="utf-8"), PAPER_HTML)

    def test_falls_back_to_ar5iv_when_official_html_is_missing(self) -> None:
        get, calls = self.fake_get({"https://ar5iv.labs.arxiv.org/html/1706.03762v7": FakeResponse(200, PAPER_HTML)})
        with mock.patch.object(kpaper.requests, "get", side_effect=get):
            payload = self.run_import("https://arxiv.org/abs/1706.03762v7")

        self.assertEqual(payload["route"], "ar5iv")
        self.assertEqual(payload["attempts"][0]["route"], "arxiv-html")
        self.assertEqual(payload["attempts"][0]["reason"], "HTTP 404")

    def test_falls_back_to_pdf_when_no_html_exists(self) -> None:
        abs_page = '<meta name="citation_title" content="Attention  Is All You Need"/>'
        get, _calls = self.fake_get({"https://arxiv.org/abs/1706.03762": FakeResponse(200, abs_page)})
        with (
            mock.patch.object(kpaper.requests, "get", side_effect=get),
            mock.patch.object(kpaper, "download_binary", return_value={"status": "wrote"}) as download,
            mock.patch.object(kpaper, "pdf_to_source_html", return_value={"status": "wrote"}) as convert,
        ):
            payload = self.run_import("1706.03762")

        self.assertEqual(payload["route"], "pdf")
        self.assertEqual([item["route"] for item in payload["attempts"]], ["arxiv-html", "ar5iv"])
        self.assertEqual(download.call_args.args[0], "https://arxiv.org/pdf/1706.03762")
        self.assertEqual(convert.call_args.kwargs["title"], "Attention Is All You Need")
        self.assertEqual(convert.call_args.kwargs["paper_id"], "arxiv-1706-03762")

    def test_source_html_never_falls_back_to_pdf(self) -> None:
        get, _calls = self.fake_get({})
        with (
            mock.patch.object(kpaper.requests, "get", side_effect=get),
            mock.patch.object(kpaper, "download_binary") as download,
            self.assertRaises(SystemExit),
        ):
            self.run_import("1706.03762", "--source", "html")
        download.assert_not_called()

    def test_source_pdf_skips_html(self) -> None:
        get, calls = self.fake_get({})
        with (
            mock.patch.object(kpaper.requests, "get", side_effect=get),
            mock.patch.object(kpaper, "download_binary", return_value={"status": "wrote"}),
            mock.patch.object(kpaper, "pdf_to_source_html", return_value={"status": "wrote"}),
        ):
            payload = self.run_import("1706.03762", "--source", "pdf")

        self.assertEqual(payload["route"], "pdf")
        self.assertEqual(payload["attempts"], [])
        self.assertNotIn("https://arxiv.org/html/1706.03762", calls)

    def test_existing_source_is_kept_without_network_unless_forced(self) -> None:
        Path("inputs").mkdir()
        Path("inputs/arxiv-1706-03762.source.html").write_text("cached", encoding="utf-8")
        with mock.patch.object(kpaper.requests, "get", side_effect=AssertionError("no network")):
            payload = self.run_import("1706.03762")
        self.assertEqual(payload["status"], "exists")

    def test_dry_run_plans_routes_without_network_or_files(self) -> None:
        with mock.patch.object(kpaper.requests, "get", side_effect=AssertionError("no network")):
            payload = self.run_import("https://arxiv.org/abs/1706.03762", "--dry-run")

        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(
            [item["url"] for item in payload["html_candidates"]],
            ["https://arxiv.org/html/1706.03762", "https://ar5iv.labs.arxiv.org/html/1706.03762"],
        )
        self.assertEqual(payload["pdf_url"], "https://arxiv.org/pdf/1706.03762")
        self.assertFalse(Path("inputs").exists())

    def test_next_commands_point_at_a_custom_output(self) -> None:
        payload = self.run_import("1706.03762", "--output", "my dir/paper.html", "--dry-run")
        self.assertEqual(
            payload["next"]["translate"],
            "./kpaper translate --paper-id arxiv-1706-03762 --input 'my dir/paper.html'",
        )

    def test_rejects_non_arxiv_url(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_import("https://example.com/paper")


if __name__ == "__main__":
    unittest.main()

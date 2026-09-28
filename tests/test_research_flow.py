"""Offline smoke test for the actual first research cycle."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "problem_research" / "cli.py"
SPEC = importlib.util.spec_from_file_location("problem_research_cli", MODULE_PATH)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class ResearchFlowTest(unittest.TestCase):
    def test_start_accepts_physics_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = ["全息理论的进展", "出现新的研究线索", "获得全景概观", "physics", ""]
            with patch.object(cli, "RESEARCH", Path(tmp) / "research"), \
                 patch.object(cli, "ask", side_effect=values):
                path = cli.start()
            problem = cli.load_json(path / "problem.json")
            self.assertEqual(problem["domain"], "physics")
            self.assertEqual(problem["domain_version"], "draft-1")

    def test_global_registry_supplies_physics_web_sources(self):
        registry = cli.source_registry()
        ids = cli.enabled_source_ids("physics", registry)
        urls = cli.registered_web_urls(registry, ids)
        self.assertIn("china_physics_institutes", ids)
        self.assertGreaterEqual(len(urls), 3)

    def test_completion_text_rejects_empty_or_reasoning_only_response(self):
        with self.assertRaisesRegex(ValueError, "推理内容"):
            cli.completion_text({"choices": [{"finish_reason": "stop", "message": {
                "content": None, "reasoning_content": "internal reasoning"}}]})

    def test_model_rejects_empty_final_text(self):
        response = {"choices": [{"message": {"content": ""}}]}
        with patch.dict("os.environ", {"QWEN_BASE_URL": "https://model.example/v1", "QWEN_MODEL": "qwen"}), \
             patch.object(cli, "http_json", return_value=response):
            with self.assertRaisesRegex(ValueError, "空文本"):
                cli.model("test")

    def test_completion_text_accepts_multipart_content(self):
        self.assertEqual(cli.completion_text({"choices": [{"message": {"content": [
            {"type": "text", "text": "{\"answer\":"}, {"type": "text", "text": " true}"}
        ]}}]}), '{"answer": true}')

    def test_completion_json_uses_final_object_after_visible_reasoning(self):
        response = 'I should consider the request first.\n\n{"draft": {"ready": true}, "count": 3}'
        self.assertEqual(cli.completion_json(response), {"draft": {"ready": True}, "count": 3})

    def test_discover_falls_back_to_crossref_after_openalex_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "research" / "study"
            path.mkdir(parents=True)
            problem = {"title": "eye context", "domain": "visual-intelligence", "required_urls": []}
            cli.save_json(path / "draft.json", {"search_query": "eye detection context"})
            fallback = [{"id": "C1", "kind": "paper_abstract", "text": "An abstract", "url": "https://doi.org/test"}]
            with patch.object(cli, "openalex_search", side_effect=ValueError("HTTP Error 429")), \
                 patch.object(cli, "crossref_search", return_value=fallback), \
                 patch.object(cli, "retrieve_paper_pages", return_value=[]):
                self.assertEqual(cli.discover(path, problem), fallback)
            self.assertEqual(cli.load_json(path / "progress.json")["stage"], "analysis")
            self.assertIn("crossref", cli.load_json(path / "sources.json")["enabled_sources"])

    def test_retrieve_paper_pages_adds_readable_landing_page(self):
        source = {"id": "P1", "kind": "paper_abstract", "title": "Eye paper", "url": "https://example.org/paper"}
        with patch.object(cli, "fetch_page", return_value="Paper page"):
            expanded = cli.retrieve_paper_pages([source])
        self.assertEqual(expanded[0]["based_on"], "P1")
        self.assertEqual(expanded[0]["text"], "Paper page")

    def test_discovery_queries_and_results_are_diverse_and_deduplicated(self):
        queries = cli.discovery_queries({"search_queries": ["holography review", "holography duality"],
                                         "search_query": "holography"}, {"title": "全息理论"})
        self.assertEqual(queries, ["holography review", "holography duality", "holography"])
        sources = []
        cli.add_index_results(sources, [{"id": "P1", "title": "One", "url": "https://doi.org/one"}], "P")
        cli.add_index_results(sources, [{"id": "C1", "title": "One", "url": "https://doi.org/one"},
                                        {"id": "C2", "title": "Two", "url": "https://doi.org/two"}], "C")
        self.assertEqual([source["id"] for source in sources], ["P1", "C1"])

    def test_unique_readable_sources_removes_duplicate_urls(self):
        sources = [{"id": "G1", "url": "https://example.org", "text": "Global"},
                   {"id": "H1", "url": "https://example.org", "text": "Manual"},
                   {"id": "P1", "url": "https://doi.org/test", "text": "Abstract"}]
        self.assertEqual([item["id"] for item in cli.unique_readable_sources(sources)], ["G1", "P1"])

    def test_access_challenge_is_not_evidence(self):
        sources = [{"id": "W1", "url": "https://example.org/a", "text": "Just a moment... Checking your browser"},
                   {"id": "H1", "url": "https://example.org/b", "text": "A readable article"}]
        self.assertEqual([s["id"] for s in cli.unique_readable_sources(sources)], ["H1"])

    def test_physics_question_requires_scope_and_progress_criterion(self):
        problem = {"title": "全息理论的进展", "domain": "physics"}
        with self.assertRaisesRegex(ValueError, "scope"):
            cli.check_frame({"question": "黑洞信息领域出现了什么进展？", "hypotheses": []}, problem)
        cli.check_frame({"question": "近年黑洞信息研究解决了哪些具体问题？", "hypotheses": [],
                         "scope": "黑洞信息与 AdS/CFT", "progress_criterion": "可复现的推导及边界"}, problem)

    def test_physics_model_system_prompt_uses_domain(self):
        response = {"choices": [{"message": {"content": "{}"}}]}
        with patch.dict("os.environ", {"QWEN_BASE_URL": "https://model.example/v1", "QWEN_MODEL": "qwen"}), \
             patch.object(cli, "http_json", return_value=response) as http:
            cli.model("test", "physics")
        self.assertIn("physics", http.call_args.args[1]["messages"][0]["content"])
        self.assertNotIn("视觉智能", http.call_args.args[1]["messages"][0]["content"])

    def test_arxiv_search_parses_atom_preprint_abstract(self):
        atom = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1234.5678</id>
        <title> Holography </title><published>2026-01-02T00:00:00Z</published><summary> A summary. </summary>
        <author><name> A. Researcher </name></author></entry></feed>'''
        with patch.object(cli, "http_text", return_value=atom):
            result = cli.arxiv_search("holography")
        self.assertEqual(result[0]["url"], "https://arxiv.org/abs/1234.5678")
        self.assertEqual(result[0]["year"], "2026")
        self.assertEqual(result[0]["text"], "A summary.")

    def test_archive_round_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study"
            path.mkdir()
            cli.save_json(path / "progress.json", {"stage": "awaiting_analysis_review"})
            cli.save_json(path / "sources.json", {"items": [{"id": "P1"}]})
            cli.save_json(path / "analysis.json", {"findings": []})
            archive = cli.archive_round(path)
            self.assertEqual(cli.load_json(archive / "sources.json")["items"][0]["id"], "P1")
            self.assertEqual(cli.load_json(archive / "round.json")["previous_stage"], "awaiting_analysis_review")

    def test_trash_research_is_recoverable_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "research"
            path = root / "study"
            path.mkdir(parents=True)
            cli.save_json(path / "problem.json", {"title": "研究"})
            with patch.object(cli, "RESEARCH", root):
                target = cli.trash_research(path)
            self.assertFalse(path.exists())
            self.assertEqual(cli.load_json(target / "problem.json")["title"], "研究")

    def test_first_cycle_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "research" / "test-study"
            path.mkdir(parents=True)
            problem = {
                "title": "眼部ROI", "observation": "整脸输入得分高", "goal": "减少误报",
                "domain": "visual-intelligence", "required_urls": ["https://example.org/article"],
            }
            cli.save_json(path / "problem.json", problem)
            cli.stage(path, "framing")
            frame = {"question": "是否因上下文", "hypotheses": [{"name": "上下文", "prediction": "配对改善"}],
                     "search_query": "eye detection context"}
            review = {"findings": [{"source_id": "H1", "claim": "需要比较", "limits": "仅网页"},
                                   {"source_id": "nonexistent", "claim": "虚构来源"}],
                      "mechanism_conclusions": [], "engineering_conclusions": [],
                      "counterpoints": ["尺度混杂"], "human_needed": ["配对图像"],
                      "next_question": "控制尺度后上下文是否有效"}
            chunk_review = {"findings": [{"source_id": "H1", "claim": "需要比较", "limits": "仅网页"}],
                            "mechanism_leads": [], "counterpoints": [], "human_needed": [], "open_questions": []}
            source = {"id": "H1", "kind": "curated_web", "title": "example",
                      "url": "https://example.org/article", "text": "观察记录"}
            with patch.object(cli, "model", side_effect=[frame, chunk_review, review]), \
                 patch.object(cli, "fetch_page", return_value="观察记录"), \
                 patch.object(cli, "openalex_search", return_value=[]), \
                 patch.object(cli, "crossref_search", return_value=[]), \
                 patch.object(cli, "approved", return_value=True):
                cli.run(path)
            current = (path / "reports" / "current.md").read_text(encoding="utf-8")
            self.assertIn("需要比较", current)
            self.assertNotIn("虚构来源", current)
            self.assertEqual(cli.load_json(path / "progress.json")["stage"], "awaiting_next_round")
            self.assertEqual(len(cli.load_json(path / "analysis.json")["findings"]), 1)
            with patch.object(cli, "model") as mocked:
                cli.run(path)
                mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()

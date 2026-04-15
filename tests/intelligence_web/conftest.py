import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from project0.intelligence_web.app import create_app
from project0.intelligence_web.config import WebConfig


@pytest.fixture
def tmp_reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir()
    return d


@pytest.fixture
def tmp_feedback_dir(tmp_path: Path) -> Path:
    # Intentionally not created — feedback append should lazily create it.
    return tmp_path / "feedback"


@pytest.fixture
def web_config(tmp_reports_dir: Path, tmp_feedback_dir: Path) -> WebConfig:
    return WebConfig(
        public_base_url="http://test.local:8080",
        bind_host="127.0.0.1",
        bind_port=8080,
        reports_dir=tmp_reports_dir,
        feedback_dir=tmp_feedback_dir,
        user_tz=ZoneInfo("Asia/Shanghai"),
    )


@pytest.fixture
def client(web_config: WebConfig) -> TestClient:
    app = create_app(web_config)
    return TestClient(app)


@pytest.fixture
def sample_report() -> dict:
    return {
        "date": "2026-04-15",
        "generated_at": "2026-04-15T08:03:22+08:00",
        "user_tz": "Asia/Shanghai",
        "watchlist_snapshot": ["openai", "sama"],
        "news_items": [
            {
                "id": "n1",
                "headline": "OpenAI 发布 o5-mini",
                "summary": "推理延迟降低 40%，对 API 用户有直接影响。",
                "importance": "high",
                "importance_reason": "主流模型迭代",
                "topics": ["ai-models"],
                "source_tweets": [
                    {
                        "handle": "sama",
                        "url": "https://x.com/sama/status/1",
                        "text": "o5-mini is here",
                        "posted_at": "2026-04-15T03:00:00Z",
                    }
                ],
            },
            {
                "id": "n2",
                "headline": "DeepMind 记忆机制论文",
                "summary": "新架构在长上下文任务上优于 baseline。",
                "importance": "medium",
                "importance_reason": "研究进展",
                "topics": ["research"],
                "source_tweets": [
                    {
                        "handle": "googledeepmind",
                        "url": "https://x.com/googledeepmind/status/2",
                        "text": "Paper",
                        "posted_at": "2026-04-15T04:00:00Z",
                    }
                ],
            },
            {
                "id": "n3",
                "headline": "Anthropic 招聘",
                "summary": "招聘信息。",
                "importance": "low",
                "importance_reason": "常规",
                "topics": ["hr"],
                "source_tweets": [
                    {
                        "handle": "anthropicai",
                        "url": "https://x.com/anthropicai/status/3",
                        "text": "Hiring",
                        "posted_at": "2026-04-15T05:00:00Z",
                    }
                ],
            },
        ],
        "suggested_accounts": [
            {
                "handle": "noamgpt",
                "reason": "被 @sama 引用，连续多日讨论推理优化",
                "seen_in_items": ["n1"],
            }
        ],
        "stats": {
            "tweets_fetched": 100,
            "handles_attempted": 2,
            "handles_succeeded": 2,
            "items_generated": 3,
            "errors": [],
        },
    }


def write_report(reports_dir: Path, report: dict) -> Path:
    path = reports_dir / f"{report['date']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def write_report_fn():
    return write_report

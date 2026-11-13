"""Research discovery connectors for Phase 5."""

from __future__ import annotations

from media_os_api.research.connectors.arxiv import ArxivConnector
from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.connectors.github import GitHubConnector
from media_os_api.research.connectors.hackernews import HackerNewsConnector
from media_os_api.research.connectors.huggingface import HuggingFaceConnector
from media_os_api.research.connectors.openai_news import OpenAINewsConnector
from media_os_api.research.connectors.reddit import RedditConnector

__all__ = [
    "ArxivConnector",
    "BaseConnector",
    "GitHubConnector",
    "HackerNewsConnector",
    "HuggingFaceConnector",
    "OpenAINewsConnector",
    "RedditConnector",
]

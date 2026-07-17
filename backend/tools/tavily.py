import os
import asyncio
from pipecat.services.llm_service import FunctionCallParams
from tavily import TavilyClient

_tavily_client = None


def _get_tavily_client():
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    return _tavily_client

async def tavily_search(params: FunctionCallParams, query: str):
    """Search the web using Tavily.
    
    Args:
        query: The search query.
    """
    if not os.getenv("TAVILY_API_KEY"):
        raise ValueError("TAVILY_API_KEY is not configured")

    def run_search():
        client = _get_tavily_client()
        return client.search(
            query=query,
            search_depth="fast",
            max_results=5,
            include_answer=True,
            include_raw_content=False,
        )
    
    result = await asyncio.to_thread(run_search)
    compact_result = {
        "query": result.get("query", query),
        "answer": result.get("answer"),
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": (item.get("content") or "")[:600],
            }
            for item in result.get("results", [])[:3]
        ],
    }
    await params.result_callback(compact_result)

import os
import asyncio
from pipecat.services.llm_service import FunctionCallParams
from tavily import TavilyClient

async def tavily_search(params: FunctionCallParams, query: str):
    """Search the web using Tavily.
    
    Args:
        query: The search query.
    """
    if not os.getenv("TAVILY_API_KEY"):
        raise ValueError("TAVILY_API_KEY is not configured")

    def run_search():
        client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        return client.search(
            query=query,
            search_depth="fast",
            max_results=5,
            include_answer=True,
            include_raw_content=False,
        )
    
    result = await asyncio.to_thread(run_search)
    await params.result_callback(result)

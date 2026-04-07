"""Web search tool — credentials injected exclusively by the gateway proxy.

The tool makes an anonymous HTTP POST to the local Atlas gateway on
localhost:18080. The gateway resolves and injects the Tavily API key
server-side. The tool never receives, stores, or transmits credentials.

If the gateway is not running, the tool returns a clear error rather than
falling back to direct credential access.
"""

from typing import Optional

import httpx
from langchain_core.tools import tool
from atlas.gateway.headers import get_gateway_headers

# URL of the local credential-vault proxy (started by atlas chat / atlas slack)
_PROXY_URL = "http://127.0.0.1:18080/v1/proxy"


def create_tavily_search_tool(api_key: Optional[str] = None, gateway=None):
    """Create the Tavily web search tool.

    The `api_key` and `gateway` arguments are accepted for API compatibility
    only and are completely ignored. All credential injection happens
    server-side in the gateway proxy.

    Returns:
        LangChain tool for web search.
    """

    @tool
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web for current information.

        Use this tool when you need to find current information, facts, news,
        or any information that may not be in your training data. Especially
        useful for:
        - Current events and news
        - Recent developments in technology, science, etc.
        - Factual information that needs verification
        - Finding specific websites or resources
        - Getting up-to-date statistics or data

        Args:
            query: The search query to look up.
            max_results: Maximum number of results to return (default: 5).

        Returns:
            Formatted string containing search results with titles, URLs,
            and content snippets.
        """
        return _search_via_proxy(query, max_results)

    return web_search


# ---------------------------------------------------------------------------
# Internal transport — proxy only, no credential access
# ---------------------------------------------------------------------------

def _search_via_proxy(query: str, max_results: int) -> str:
    """POST to the local credential vault proxy. No key ever touches this code."""
    payload = {
        "service": "tavily",
        "method": "POST",
        "endpoint": "/search",
        "json_body": {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": True,
            "include_raw_content": False,
        },
    }
    try:
        resp = httpx.post(_PROXY_URL, json=payload, headers=get_gateway_headers(), timeout=30.0)
        resp.raise_for_status()
        return _format_response(resp.json(), query)
    except httpx.ConnectError:
        return (
            "Web search unavailable: the Atlas credential gateway is not running. "
            "Start 'atlas chat' or 'atlas slack' to launch it automatically."
        )
    except httpx.HTTPStatusError as exc:
        return f"Web search error {exc.response.status_code}: {exc.response.text}"
    except Exception as exc:
        return f"Web search proxy error: {exc}"


def _format_response(data: dict, query: str) -> str:
    """Format Tavily response into readable markdown."""
    if not isinstance(data, dict):
        return str(data) if data else f"No results found for query: {query}"

    results = []

    if data.get("answer"):
        results.append(f"**Quick Answer:**\n{data['answer']}\n")

    if data.get("results"):
        results.append("**Search Results:**\n")
        for i, result in enumerate(data["results"], 1):
            title = result.get("title", "No title")
            url = result.get("url", "")
            content = result.get("content", "No content available")
            results.append(
                f"{i}. **{title}**\n"
                f"   URL: {url}\n"
                f"   {content}\n"
            )

    if not results:
        return f"No results found for query: {query}"

    return "\n".join(results)

"""Local LLM adapter for Ollama."""

import httpx
from typing import AsyncIterator


class OllamaAdapter:
    """Adapter for local LLM inference via Ollama.
    
    Ollama provides a simple REST API for running LLMs locally.
    This adapter wraps that API for use with LangChain/LangGraph.
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:14b",
        timeout: float = 120.0,
    ):
        """Initialize Ollama adapter.
        
        Args:
            base_url: Ollama server URL
            model: Default model to use
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        stream: bool = False,
    ) -> str | AsyncIterator[str]:
        """Generate a completion from Ollama.
        
        Args:
            prompt: The prompt to complete
            model: Model to use (defaults to self.model)
            system: Optional system prompt
            stream: Whether to stream the response
            
        Returns:
            Generated text or async iterator of chunks
        """
        client = await self._get_client()
        
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": stream,
        }
        
        if system:
            payload["system"] = system
        
        if stream:
            return self._stream_generate(client, payload)
        else:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
    
    async def _stream_generate(
        self,
        client: httpx.AsyncClient,
        payload: dict,
    ) -> AsyncIterator[str]:
        """Stream generation response."""
        import json
        
        async with client.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if chunk := data.get("response"):
                            yield chunk
                    except json.JSONDecodeError:
                        continue
    
    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = False,
    ) -> dict | AsyncIterator[dict]:
        """Chat completion with message history.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            stream: Whether to stream
            
        Returns:
            Response message dict or async iterator
        """
        client = await self._get_client()
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
        }
        
        if stream:
            return self._stream_chat(client, payload)
        else:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("message", {})
    
    async def _stream_chat(
        self,
        client: httpx.AsyncClient,
        payload: dict,
    ) -> AsyncIterator[dict]:
        """Stream chat response."""
        import json
        
        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if msg := data.get("message"):
                            yield msg
                    except json.JSONDecodeError:
                        continue
    
    async def is_available(self) -> bool:
        """Check if Ollama server is available."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False
    
    async def list_models(self) -> list[str]:
        """List available models."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

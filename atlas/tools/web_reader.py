"""Web Reader tool for extracting content from URLs."""

import trafilatura
from langchain_core.tools import tool


def create_web_reader_tool():
    """Create the Web Reader tool.
    
    Returns:
        List of LangChain tools for reading web content
    """
    
    @tool
    def read_url(url: str) -> str:
        """Read and extract the main content from a web page URL.
        
        Use this tool when you need to read an article, blog post, or documentation page
        to answer a user's question. It extracts the main text and removes ads/navigation.
        
        Args:
            url: The URL of the web page to read
            
        Returns:
            The extracted text content of the page, or an error message.
        """
        try:
            # Download the page
            downloaded = trafilatura.fetch_url(url)
            
            if downloaded is None:
                return f"Error: Could not fetch content from {url}. The URL might be invalid or the site is blocking access."
            
            # Extract content
            result = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False
            )
            
            if result is None:
                return f"Error: Could not extract meaningful content from {url}."
            
            # Extract metadata if possible
            metadata = trafilatura.extract_metadata(downloaded)
            meta_str = ""
            if metadata:
                title = metadata.title or "Unknown Title"
                author = metadata.author or "Unknown Author"
                date = metadata.date or "Unknown Date"
                site = metadata.sitename or "Unknown Site"
                meta_str = f"# {title}\n**Source:** {site} | **Author:** {author} | **Date:** {date}\n\n"
            
            return f"{meta_str}{result}"
            
        except Exception as e:
            return f"Error reading URL: {str(e)}"

    return [read_url]

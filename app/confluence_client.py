"""
Confluence Cloud API client for creating and updating meeting pages.
Uses Confluence REST API v1.

Base URL should be https://<site>.atlassian.net/wiki (with /wiki).
If /wiki is not present, it is automatically appended.
"""
import logging
import re
from typing import Optional, Dict, Any, List

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_not_exception_type

from app.config import settings

logger = logging.getLogger(__name__)


def _safe_confluence_request(method: str, url: str, **kwargs) -> Dict[str, Any]:
    """
    Make a safe Confluence request that handles HTML responses gracefully.
    
    Never raises exceptions on HTML responses - returns a fallback dict instead.
    Also handles JSON error responses with statusCode field.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        url: Full URL to request
        **kwargs: Additional arguments for requests.request
        
    Returns:
        JSON dict if successful, or fallback dict with error info
    """
    try:
        resp = requests.request(method, url, **kwargs)
    except requests.exceptions.RequestException as e:
        logger.error(f"Confluence request failed: {e}")
        return {
            "fallback": True,
            "status_code": 0,
            "html_title": "Request Error",
            "html_snippet": str(e)[:300],
        }
    
    content_type = resp.headers.get("Content-Type", "")
    
    # JSON case - try to parse
    if "application/json" in content_type:
        try:
            data = resp.json()
            
            # Check for JSON error responses with statusCode field (Confluence error format)
            if isinstance(data, dict) and data.get("statusCode"):
                status_code = int(data.get("statusCode", 0))
                message = data.get("message", "Unknown error")
                
                if status_code == 403:
                    logger.warning(f"Confluence 403 permission denied: {message}")
                    return {
                        "fallback": True,
                        "status_code": 403,
                        "html_title": "Permission Denied",
                        "html_snippet": message,
                    }
                elif status_code >= 400:
                    logger.warning(f"Confluence error {status_code}: {message}")
                    return {
                        "fallback": True,
                        "status_code": status_code,
                        "html_title": f"Error {status_code}",
                        "html_snippet": message,
                    }
            
            return data
        except Exception as e:
            logger.warning(f"Failed to parse JSON response: {e}")
    
    # HTML fallback - extract useful info with BeautifulSoup
    html = resp.text or ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else "No title"
        snippet = soup.get_text(" ", strip=True)[:300]
    except Exception:
        title = "Parse Error"
        snippet = html[:300] if html else "No response body"
    
    logger.warning(f"Confluence HTML response ({resp.status_code}): {title}")
    
    return {
        "fallback": True,
        "status_code": resp.status_code,
        "html_title": title,
        "html_snippet": snippet,
    }


class ConfluenceClient:
    """
    Client for interacting with Confluence Cloud REST API v1.
    
    Base URL should include /wiki:
        https://<site>.atlassian.net/wiki
    
    If /wiki is not present, it is automatically appended.
    """
    
    # API path prefix for Confluence REST API v1 calls (after /wiki)
    API_PREFIX = "/rest/api"
    
    def __init__(self):
        """Initialize the Confluence client with configuration."""
        # Normalize base URL: ensure it ends with /wiki
        base_url = settings.confluence_base_url.rstrip('/')
        if not base_url.endswith("/wiki"):
            base_url = base_url + "/wiki"
        self.base_url = base_url
        
        self.email = settings.confluence_email
        self.api_token = settings.confluence_api_token
        self.space_key = settings.confluence_space_key
        
        # Auth tuple for basic auth (email, api_token)
        self.auth = (self.email, self.api_token) if self.email and self.api_token else None
        self._space_verified: bool = False
        
        logger.info(f"Confluence client initialized for: {self.base_url} (space: {self.space_key})")
    
    @property
    def is_configured(self) -> bool:
        """Check if the client is properly configured."""
        return bool(
            self.base_url and 
            self.email and 
            self.api_token and 
            self.space_key and
            self.auth
        )
    
    @property
    def headers(self) -> Dict[str, str]:
        """Get standard headers for API requests."""
        return {"Accept": "application/json"}
    
    def _api_url(self, endpoint: str) -> str:
        """
        Build full API URL for an endpoint.
        
        Args:
            endpoint: API endpoint path (e.g., "/content" or "/space/MYSPACE")
            
        Returns:
            Full URL with base_url + /rest/api + endpoint
        """
        if not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        return f"{self.base_url}{self.API_PREFIX}{endpoint}"
    
    def _safe_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Make an authenticated request to the Confluence API using the safe wrapper.
        
        Never raises on HTML responses - returns fallback dict instead.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., "/content", "/space/MYSPACE")
            params: Optional query parameters
            json_data: Optional JSON body for POST/PUT
            timeout: Request timeout in seconds
            
        Returns:
            JSON dict if successful, or fallback dict with HTML info
        """
        if not self.auth:
            logger.error("Confluence client not authenticated")
            return {
                "fallback": True,
                "status_code": 0,
                "html_title": "Not Authenticated",
                "html_snippet": "Check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN.",
            }
        
        url = self._api_url(endpoint)
        return _safe_confluence_request(
            method=method.upper(),
            url=url,
            params=params,
            json=json_data,
            headers=self.headers,
            auth=self.auth,
            timeout=timeout
        )
    
    def _verify_space_access(self) -> bool:
        """
        Verify API user has access to the configured space.
        
        Returns:
            True if space is accessible, False otherwise (no exceptions)
        """
        if not self.is_configured:
            return False
        
        # Return cached result if already verified
        if self._space_verified:
            return True
        
        url = self._api_url(f"/space/{self.space_key}")
        data = _safe_confluence_request(
            "GET", url,
            headers=self.headers,
            auth=self.auth,
            timeout=30
        )
        
        if data.get("fallback"):
            logger.warning(f"Skipping space verification due to HTML response: {data.get('html_title')}")
            return False
        
        space_name = data.get("name", self.space_key)
        logger.info(f"Verified access to Confluence space: {space_name} ({self.space_key})")
        self._space_verified = True
        return True
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(RuntimeError)
    )
    def find_page_by_title(self, title: str) -> Optional[str]:
        """
        Find a Confluence page by title within the configured space.
        
        Args:
            title: Page title to search for
            
        Returns:
            Page ID if found, None otherwise
        """
        if not self.is_configured:
            logger.warning("Confluence client not configured")
            return None
        
        url = self._api_url("/content")
        params = {
            "spaceKey": self.space_key,
            "title": title,
            "type": "page",
            "limit": 1
        }
        
        data = _safe_confluence_request(
            "GET", url,
            params=params,
            headers=self.headers,
            auth=self.auth,
            timeout=30
        )
        
        if data.get("fallback"):
            logger.warning(f"Skipping find_page_by_title due to HTML response: {data.get('html_title')}")
            return None
        
        results = data.get("results", [])
        if results:
            page_id = results[0].get("id")
            logger.info(f"Found existing page '{title}' with ID: {page_id}")
            return page_id
        
        logger.debug(f"No existing page found with title: {title}")
        return None
    
    def _get_page_version(self, page_id: str) -> int:
        """
        Get the current version number of a page.
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            Current page version number (defaults to 1 on errors)
        """
        url = self._api_url(f"/content/{page_id}")
        params = {"expand": "version"}
        
        data = _safe_confluence_request(
            "GET", url,
            params=params,
            headers=self.headers,
            auth=self.auth,
            timeout=30
        )
        
        if data.get("fallback"):
            logger.warning(f"Failed to get page version for {page_id}, defaulting to 1")
            return 1
        
        version = data.get("version", {}).get("number", 1)
        return version
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(RuntimeError)
    )
    def create_page(self, title: str, html: str) -> Optional[Dict[str, Any]]:
        """
        Create a new Confluence page.
        
        Args:
            title: Page title
            html: HTML content for the page body
            
        Returns:
            Dictionary with page_id and page_url, or None on failure
        """
        if not self.is_configured:
            logger.warning("Confluence client not configured")
            return None
        
        # Verify space access first
        if not self._verify_space_access():
            logger.warning("Skipping page creation - space access not verified")
            return None
        
        url = self._api_url("/content")
        
        payload = {
            "type": "page",
            "title": title,
            "space": {
                "key": self.space_key
            },
            "body": {
                "storage": {
                    "value": html,
                    "representation": "storage"
                }
            }
        }
        
        data = _safe_confluence_request(
            "POST", url,
            json=payload,
            headers=self.headers,
            auth=self.auth,
            timeout=60
        )
        
        # Check for failed or fallback response
        if not data or data.get("fallback"):
            logger.error(f"Confluence create_page failed or returned fallback: {data}")
            return None
        
        # Extract page ID from root level (Confluence returns "id" as string at root)
        page_id = data.get("id")
        
        if not page_id:
            logger.error(f"Confluence response missing 'id' field. Full response: {data}")
            return None
        
        # Get page URL from response or construct it
        page_url = data.get("_links", {}).get("webui", "")
        if page_url:
            page_url = f"{self.base_url}{page_url}"
        else:
            page_url = f"{self.base_url}/spaces/{self.space_key}/pages/{page_id}"
        
        logger.info(f"Created Confluence page: {title} (ID: {page_id})")
        logger.info(f"Confluence page URL: {page_url}")
        
        return {
            "page_id": page_id,
            "page_url": page_url,
            "title": title
        }
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(RuntimeError)
    )
    def update_page(self, page_id: str, html: str, title: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Update an existing Confluence page.
        
        Args:
            page_id: Page ID to update
            html: New HTML content for the page body
            title: Optional new title (required for v1 API)
            
        Returns:
            Dictionary with page_id and page_url, or None on failure
        """
        if not self.is_configured:
            logger.warning("Confluence client not configured")
            return None
        
        # Get current page info (need title if not provided)
        current_version = self._get_page_version(page_id)
        
        # If no title provided, fetch current title
        if not title:
            url = self._api_url(f"/content/{page_id}")
            data = _safe_confluence_request(
                "GET", url,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            if not data or data.get("fallback"):
                title = "Untitled"
            else:
                title = data.get("title", "Untitled")
        
        url = self._api_url(f"/content/{page_id}")
        
        payload = {
            "type": "page",
            "title": title,
            "space": {
                "key": self.space_key
            },
            "version": {
                "number": current_version + 1
            },
            "body": {
                "storage": {
                    "value": html,
                    "representation": "storage"
                }
            }
        }
        
        data = _safe_confluence_request(
            "PUT", url,
            json=payload,
            headers=self.headers,
            auth=self.auth,
            timeout=60
        )
        
        # Check for failed or fallback response
        if not data or data.get("fallback"):
            logger.error(f"Confluence update_page failed or returned fallback: {data}")
            return None
        
        # Verify the response contains expected data
        updated_page_id = data.get("id")
        if not updated_page_id:
            logger.error(f"Confluence update response missing 'id' field. Full response: {data}")
            return None
        
        page_title = data.get("title", title)
        
        # Get page URL from response or construct it
        page_url = data.get("_links", {}).get("webui", "")
        if page_url:
            page_url = f"{self.base_url}{page_url}"
        else:
            page_url = f"{self.base_url}/spaces/{self.space_key}/pages/{page_id}"
        
        logger.info(f"Updated Confluence page: {page_title} (ID: {page_id}, version: {current_version + 1})")
        
        return {
            "page_id": page_id,
            "page_url": page_url,
            "title": page_title,
            "version": current_version + 1
        }
    
    def create_or_update_page(self, title: str, html: str) -> Optional[Dict[str, Any]]:
        """
        Create a new page or update if one with the same title exists.
        
        Args:
            title: Page title
            html: HTML content
            
        Returns:
            Dictionary with page_id, page_url, and action (created/updated), or None on failure
        """
        existing_page_id = self.find_page_by_title(title)
        
        if existing_page_id:
            result = self.update_page(existing_page_id, html, title)
            if result:
                result["action"] = "updated"
        else:
            result = self.create_page(title, html)
            if result:
                result["action"] = "created"
        
        return result


# Singleton pattern for client reuse
_confluence_client: Optional[ConfluenceClient] = None


def get_confluence_client() -> ConfluenceClient:
    """
    Get or create the Confluence client singleton.
    
    Returns:
        ConfluenceClient instance
    """
    global _confluence_client
    if _confluence_client is None:
        _confluence_client = ConfluenceClient()
    return _confluence_client


def build_meeting_page_html(
    title: str,
    meeting_date: str,
    summary: str,
    key_points: Optional[List[str]] = None,
    decisions: Optional[List[str]] = None,
    action_items: Optional[List[Dict[str, str]]] = None,
    transcript: Optional[str] = None,
    jira_base_url: Optional[str] = None
) -> str:
    """
    Build HTML content for a meeting Confluence page.
    
    Args:
        title: Meeting title
        meeting_date: Meeting date string
        summary: Meeting summary
        key_points: List of key discussion points
        decisions: List of decisions made
        action_items: List of action items with jira_key and description
        transcript: Full meeting transcript
        jira_base_url: Base URL for Jira links
        
    Returns:
        HTML string formatted for Confluence storage format
    """
    html_parts = []
    
    # Title and date
    html_parts.append(f"<h1>{_escape_html(title)}</h1>")
    html_parts.append(f"<p><strong>Date:</strong> {_escape_html(meeting_date)}</p>")
    html_parts.append("<hr/>")
    
    # Summary section
    html_parts.append("<h2>Summary</h2>")
    html_parts.append(f"<p>{_escape_html(summary)}</p>")
    
    # Key Points section
    if key_points:
        html_parts.append("<h2>Key Points</h2>")
        html_parts.append("<ul>")
        for point in key_points:
            html_parts.append(f"<li>{_escape_html(point)}</li>")
        html_parts.append("</ul>")
    
    # Decisions section
    if decisions:
        html_parts.append("<h2>Decisions</h2>")
        html_parts.append("<ul>")
        for decision in decisions:
            html_parts.append(f"<li>{_escape_html(decision)}</li>")
        html_parts.append("</ul>")
    
    # Action Items section with Jira links
    if action_items:
        html_parts.append("<h2>Action Items</h2>")
        html_parts.append("<ul>")
        for item in action_items:
            jira_key = item.get("jira_key", "")
            description = item.get("description", "")
            assignee = item.get("assignee", "Unassigned")
            
            if jira_key and jira_base_url:
                jira_url = f"{jira_base_url}/browse/{jira_key}"
                html_parts.append(
                    f'<li><a href="{jira_url}">{_escape_html(jira_key)}</a> — '
                    f'{_escape_html(description)} ({_escape_html(assignee)})</li>'
                )
            else:
                html_parts.append(
                    f"<li>{_escape_html(description)} ({_escape_html(assignee)})</li>"
                )
        html_parts.append("</ul>")
    
    # Transcript section (collapsible)
    if transcript:
        html_parts.append("<h2>Transcript</h2>")
        html_parts.append(
            '<ac:structured-macro ac:name="expand">'
            '<ac:parameter ac:name="title">Click to expand full transcript</ac:parameter>'
            '<ac:rich-text-body>'
        )
        # Format transcript with paragraph breaks
        transcript_paragraphs = transcript.split('\n\n')
        for para in transcript_paragraphs:
            if para.strip():
                html_parts.append(f"<p>{_escape_html(para.strip())}</p>")
        html_parts.append('</ac:rich-text-body></ac:structured-macro>')
    
    return "\n".join(html_parts)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_simple_meeting_page(
    meeting_date: str,
    summary: str,
    action_items: Optional[List[Dict[str, str]]] = None,
    transcript: Optional[str] = None,
    jira_base_url: Optional[str] = None
) -> str:
    """
    Build a simple HTML meeting page.
    
    Args:
        meeting_date: Meeting date string
        summary: Meeting summary
        action_items: List of action items with jira_key, description, assignee
        transcript: Full meeting transcript
        jira_base_url: Base URL for Jira links
        
    Returns:
        HTML string
    """
    html_parts = []
    
    # Header
    html_parts.append(f"<h1>Meeting {_escape_html(meeting_date)}</h1>")
    
    # Summary section
    html_parts.append("<h2>Summary</h2>")
    html_parts.append(f"<p>{_escape_html(summary)}</p>")
    
    # Action Items section
    if action_items:
        html_parts.append("<h2>Action Items</h2>")
        html_parts.append("<ul>")
        for item in action_items:
            jira_key = item.get("jira_key", "")
            description = item.get("description", "")
            assignee = item.get("assignee", "Unassigned")
            
            if jira_key and jira_base_url:
                jira_url = f"{jira_base_url}/browse/{jira_key}"
                html_parts.append(
                    f'<li><a href="{jira_url}">{_escape_html(jira_key)}</a> — '
                    f'{_escape_html(description)} ({_escape_html(assignee)})</li>'
                )
            else:
                html_parts.append(
                    f"<li>{_escape_html(description)} ({_escape_html(assignee)})</li>"
                )
        html_parts.append("</ul>")
    
    # Transcript section
    if transcript:
        html_parts.append("<h2>Transcript</h2>")
        html_parts.append(f"<p>{_escape_html(transcript)}</p>")
    
    return "\n".join(html_parts)

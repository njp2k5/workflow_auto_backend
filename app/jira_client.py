"""
Jira Cloud API client for creating issues and managing tasks.
"""
import logging
import re
from typing import Optional, List, Dict, Any
from functools import lru_cache
from difflib import SequenceMatcher

import requests
from requests.auth import HTTPBasicAuth
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

# Team members for fuzzy matching
TEAM_MEMBERS = [
    "Nikhil J Prasad",
    "Kailas S S",
    "S Govind Krishnan",
    "Mukundan V S"
]


def find_closest_team_member(name: str) -> Optional[str]:
    """
    Find the closest matching team member name using fuzzy matching.
    
    Args:
        name: Name to match
        
    Returns:
        Closest team member name or None if no good match
    """
    if not name or name.lower() in ['unassigned', 'none', 'null']:
        return None
    
    name_lower = name.lower()
    best_match = None
    best_ratio = 0.0
    
    for member in TEAM_MEMBERS:
        # Check if any part of the name matches
        member_parts = member.lower().split()
        name_parts = name_lower.split()
        
        # Direct substring match
        if name_lower in member.lower() or any(part in member.lower() for part in name_parts):
            ratio = 0.8
        else:
            ratio = SequenceMatcher(None, name_lower, member.lower()).ratio()
        
        # Also check individual parts
        for part in name_parts:
            for member_part in member_parts:
                part_ratio = SequenceMatcher(None, part, member_part).ratio()
                if part_ratio > ratio:
                    ratio = part_ratio
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = member
    
    # Require at least 50% match
    if best_ratio >= 0.5:
        logger.info(f"Matched '{name}' to team member '{best_match}' (similarity: {best_ratio:.2f})")
        return best_match
    
    logger.warning(f"No team member match found for '{name}'")
    return None


class JiraClient:
    """
    Client for interacting with Jira Cloud REST API v3.
    Handles issue creation and user management.
    """
    
    def __init__(self):
        """Initialize the Jira client with configuration."""
        self.server = settings.jira_server.rstrip('/')
        self.email = settings.jira_email
        self.api_token = settings.jira_api_token
        self.project_key = settings.jira_project_key
        
        self._auth = HTTPBasicAuth(self.email, self.api_token) if self.email and self.api_token else None
        self._user_cache: Dict[str, str] = {}  # Cache for display name -> account ID mapping
        self._issue_types_cache: Optional[List[str]] = None  # Cache for valid issue types
        
        logger.info(f"Jira client initialized for server: {self.server}")
    
    @property
    def is_configured(self) -> bool:
        """Check if the client is properly configured."""
        return bool(self.server and self.email and self.api_token and self.project_key)
    
    @property
    def headers(self) -> Dict[str, str]:
        """Get standard headers for API requests."""
        return {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
    
    def get_project_issue_types(self) -> List[str]:
        """
        Get valid issue types for the project.
        
        Returns:
            List of valid issue type names
        """
        if self._issue_types_cache is not None:
            return self._issue_types_cache
        
        try:
            url = f"{self.server}/rest/api/3/project/{self.project_key}"
            response = requests.get(
                url,
                headers=self.headers,
                auth=self._auth,
                timeout=30
            )
            response.raise_for_status()
            project_data = response.json()
            
            issue_types = [it["name"] for it in project_data.get("issueTypes", [])]
            self._issue_types_cache = issue_types
            logger.info(f"Available issue types for {self.project_key}: {issue_types}")
            return issue_types
            
        except Exception as e:
            logger.error(f"Error getting project issue types: {e}")
            return []
    
    def get_valid_issue_type(self, preferred: str = "Task") -> str:
        """
        Get a valid issue type, falling back to available types.
        
        Args:
            preferred: Preferred issue type name
            
        Returns:
            A valid issue type name
        """
        issue_types = self.get_project_issue_types()
        
        if not issue_types:
            return preferred  # Return preferred and let API fail with better error
        
        # Check if preferred type exists
        for it in issue_types:
            if it.lower() == preferred.lower():
                return it
        
        # Fallback priority: Story > Bug > first available
        fallbacks = ["Story", "Bug", "Sub-task"]
        for fb in fallbacks:
            for it in issue_types:
                if it.lower() == fb.lower():
                    logger.info(f"Using fallback issue type: {it} ('{preferred}' not available)")
                    return it
        
        # Use first available
        logger.info(f"Using first available issue type: {issue_types[0]}")
        return issue_types[0]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def search_users(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for users by display name or email.
        
        Args:
            query: Search query (display name or email)
            
        Returns:
            List of matching users
        """
        if not self.is_configured:
            raise RuntimeError("Jira client not configured")
        
        url = f"{self.server}/rest/api/3/user/search"
        params = {"query": query, "maxResults": 10}
        
        try:
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                auth=self._auth,
                timeout=30
            )
            response.raise_for_status()
            users = response.json()
            
            logger.debug(f"Found {len(users)} users matching '{query}'")
            return users
            
        except requests.RequestException as e:
            logger.error(f"Error searching users: {e}")
            raise
    
    def get_account_id_by_name(self, display_name: str) -> Optional[str]:
        """
        Get Jira account ID by display name.
        Uses cache to minimize API calls.
        
        Args:
            display_name: User's display name
            
        Returns:
            Account ID or None if not found
        """
        # Check cache first
        if display_name in self._user_cache:
            return self._user_cache[display_name]
        
        try:
            users = self.search_users(display_name)
            
            # Try exact match first
            for user in users:
                if user.get('displayName', '').lower() == display_name.lower():
                    account_id = user.get('accountId')
                    if account_id:
                        self._user_cache[display_name] = account_id
                    return account_id
            
            # Fall back to first result if available
            if users:
                account_id = users[0].get('accountId')
                if account_id:
                    self._user_cache[display_name] = account_id
                logger.info(f"Using approximate match for '{display_name}': {users[0].get('displayName')}")
                return account_id
            
            logger.warning(f"No user found for display name: {display_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting account ID for '{display_name}': {e}")
            return None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def create_issue(
        self,
        summary: str,
        description: Optional[str] = None,
        issue_type: str = "Task",
        assignee_name: Optional[str] = None,
        due_date: Optional[str] = None,
        labels: Optional[List[str]] = None,
        priority: Optional[str] = None,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a new Jira issue.
        
        Args:
            summary: Issue summary/title
            description: Issue description
            issue_type: Type of issue (Task, Story, Bug, etc.)
            assignee_name: Display name of assignee
            due_date: Due date in YYYY-MM-DD format
            labels: List of labels
            priority: Priority name (Highest, High, Medium, Low, Lowest)
            custom_fields: Additional custom fields
            
        Returns:
            Created issue details including key
        """
        if not self.is_configured:
            raise RuntimeError("Jira client not configured")
        
        url = f"{self.server}/rest/api/3/issue"
        
        # Get valid issue type
        valid_issue_type = self.get_valid_issue_type(issue_type)
        
        # Build issue fields
        fields = {
            "project": {"key": self.project_key},
            "summary": summary,
            "issuetype": {"name": valid_issue_type}
        }
        
        # Add description in ADF format
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": description
                            }
                        ]
                    }
                ]
            }
        
        # Add assignee if provided
        if assignee_name:
            account_id = self.get_account_id_by_name(assignee_name)
            if account_id:
                fields["assignee"] = {"accountId": account_id}
            else:
                logger.warning(f"Could not find assignee '{assignee_name}', creating unassigned issue")
        
        # Add due date (validate format first)
        if due_date:
            # Validate YYYY-MM-DD format
            if re.match(r'^\d{4}-\d{2}-\d{2}$', str(due_date)):
                fields["duedate"] = due_date
            else:
                logger.warning(f"Invalid due_date format '{due_date}', skipping")
        
        # Add labels
        if labels:
            fields["labels"] = labels
        
        # Add priority
        if priority:
            fields["priority"] = {"name": priority}
        
        # Add custom fields
        if custom_fields:
            fields.update(custom_fields)
        
        payload = {"fields": fields}
        
        try:
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                auth=self._auth,
                timeout=30
            )
            response.raise_for_status()
            issue = response.json()
            
            logger.info(f"Created Jira issue: {issue.get('key')}")
            return issue
            
        except requests.RequestException as e:
            logger.error(f"Error creating Jira issue: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise
    
    def create_issues_from_tasks(
        self,
        tasks: List[Dict[str, Any]],
        meeting_context: Optional[str] = None
    ) -> List[str]:
        """
        Create Jira issues from extracted tasks.
        
        Args:
            tasks: List of task dictionaries with title, assignee, due_date
            meeting_context: Optional context about the meeting for descriptions
            
        Returns:
            List of created issue keys
        """
        created_keys = []
        
        for task in tasks:
            title = task.get('title', 'Untitled Task')
            assignee = task.get('assignee')
            due_date = task.get('due_date')
            
            # Build description
            description_parts = []
            if meeting_context:
                description_parts.append(f"From meeting: {meeting_context}")
            if assignee:
                description_parts.append(f"Assigned to: {assignee}")
            if due_date:
                description_parts.append(f"Due: {due_date}")
            
            description = "\n".join(description_parts) if description_parts else None
            
            try:
                issue = self.create_issue(
                    summary=title,
                    description=description,
                    assignee_name=assignee,
                    due_date=due_date,
                    labels=["meeting-action-item"]
                )
                created_keys.append(issue.get('key'))
                
            except Exception as e:
                logger.error(f"Failed to create issue for task '{title}': {e}")
                continue
        
        logger.info(f"Created {len(created_keys)} Jira issues")
        return created_keys
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """
        Get details of a specific issue.
        
        Args:
            issue_key: Issue key (e.g., PROJ-123)
            
        Returns:
            Issue details
        """
        if not self.is_configured:
            raise RuntimeError("Jira client not configured")
        
        url = f"{self.server}/rest/api/3/issue/{issue_key}"
        
        try:
            response = requests.get(
                url,
                headers=self.headers,
                auth=self._auth,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
            
        except requests.RequestException as e:
            logger.error(f"Error getting issue {issue_key}: {e}")
            raise
    
    def test_connection(self) -> bool:
        """
        Test the Jira connection by fetching current user.
        
        Returns:
            True if connection is successful
        """
        if not self.is_configured:
            return False
        
        url = f"{self.server}/rest/api/3/myself"
        
        try:
            response = requests.get(
                url,
                headers=self.headers,
                auth=self._auth,
                timeout=30
            )
            response.raise_for_status()
            user = response.json()
            logger.info(f"Jira connection successful. Authenticated as: {user.get('displayName')}")
            return True
            
        except Exception as e:
            logger.error(f"Jira connection test failed: {e}")
            return False


# Singleton instance
_jira_client: Optional[JiraClient] = None


def get_jira_client() -> JiraClient:
    """Get or create the singleton Jira client instance."""
    global _jira_client
    if _jira_client is None:
        _jira_client = JiraClient()
    return _jira_client

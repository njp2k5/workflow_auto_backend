"""
LLM interface using Groq API for meeting summarization and task extraction.
"""
import json
import logging
from typing import Optional, List, Dict, Any

# Try to import langchain_groq, but allow fallback if not installed
try:
    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import SecretStr
    LANGCHAIN_AVAILABLE = True
except ImportError:
    ChatGroq = None  # type: ignore
    HumanMessage = None  # type: ignore
    SystemMessage = None  # type: ignore
    SecretStr = None  # type: ignore
    LANGCHAIN_AVAILABLE = False

from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM interface using Groq API with LangChain.
    Handles meeting summarization and task extraction.
    """
    
    def __init__(self):
        """Initialize the LLM client with Groq."""
        self.model_name = settings.groq_model
        self.api_key = settings.groq_api_key
        self._llm = None
        
        if not LANGCHAIN_AVAILABLE:
            logger.warning("langchain-groq not installed. LLM features disabled.")
            return
        
        if self.api_key:
            self._llm = ChatGroq(  # type: ignore[misc]
                model=self.model_name,
                temperature=0.2,
                api_key=SecretStr(self.api_key),  # type: ignore[misc]
                max_retries=3
            )
            logger.info(f"LLM client initialized with model: {self.model_name}")
        else:
            logger.warning("Groq API key not configured")
    
    @property
    def is_configured(self) -> bool:
        """Check if the LLM is properly configured."""
        return self._llm is not None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def summarize_meeting(self, transcript: str, max_length: int = 1000) -> str:
        """
        Generate a brief summary of the meeting transcript.
        Optimized for short transcripts (prototype).
        
        Args:
            transcript: Meeting transcript (typically 2-4 sentences for prototype)
            max_length: Maximum length of transcript to process
            
        Returns:
            Meeting summary text
        """
        if not self.is_configured:
            raise RuntimeError("LLM client not configured")
        
        system_prompt = """Summarize the meeting in 1-2 sentences. Be direct and concise."""

        user_prompt = f"""Transcript:\n{transcript}\n\nSummary:"""

        try:
            messages = [
                SystemMessage(content=system_prompt),  # type: ignore[misc]
                HumanMessage(content=user_prompt)  # type: ignore[misc]
            ]
            
            response = self._llm.invoke(messages)  # type: ignore
            content = response.content
            summary = content if isinstance(content, str) else str(content)
            summary = summary.strip()
            
            logger.info(f"Generated meeting summary ({len(summary)} chars)")
            return summary
            
        except Exception as e:
            logger.error(f"Error generating meeting summary: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def extract_tasks(self, transcript: str, summary: Optional[str] = None) -> Dict[str, Any]:
        """
        Extract action items/tasks from the meeting transcript.
        Optimized for short transcripts (prototype).
        
        Args:
            transcript: Meeting transcript (typically 2-4 sentences for prototype)
            summary: Optional meeting summary for additional context
            
        Returns:
            Dictionary with tasks list in the specified format
        """
        if not self.is_configured:
            raise RuntimeError("LLM client not configured")
        
        system_prompt = """You are a JSON extraction assistant. Your ONLY job is to extract tasks and return valid JSON.

RULES:
1. Output ONLY valid JSON - no explanations, no markdown, no text before or after
2. Every response must start with { and end with }
3. Use this exact format: {"tasks": [{"title": "task description", "assignee": "person name", "due_date": "YYYY-MM-DD"}]}
4. If no clear assignee, use "Unassigned"
5. If no due date mentioned, use null
6. If no tasks found, return: {"tasks": []}
7. Look for action words like: will, should, needs to, assigned to, responsible for"""

        user_prompt = f"""Extract all tasks/action items from this transcript and return ONLY JSON:

{transcript}

Respond with JSON only:"""

        try:
            messages = [
                SystemMessage(content=system_prompt),  # type: ignore[misc]
                HumanMessage(content=user_prompt)  # type: ignore[misc]
            ]
            
            response = self._llm.invoke(messages)  # type: ignore
            content = response.content
            response_text = content if isinstance(content, str) else str(content)
            response_text = response_text.strip()
            
            # Log raw response for debugging
            logger.debug(f"Raw LLM response: {response_text[:500]}")
            
            # Parse JSON from response
            tasks_data = self._parse_json_response(response_text)
            
            # If still empty, try regex extraction from transcript
            if not tasks_data.get("tasks") and transcript:
                tasks_data = self._extract_tasks_fallback(transcript)
            
            # Validate structure
            if "tasks" not in tasks_data:
                tasks_data = {"tasks": []}
            
            # Validate each task
            validated_tasks = []
            for task in tasks_data.get("tasks", []):
                validated_task = {
                    "title": task.get("title", "Untitled Task"),
                    "assignee": task.get("assignee") or "Unassigned",
                    "due_date": task.get("due_date")  # Can be None
                }
                validated_tasks.append(validated_task)
            
            result = {"tasks": validated_tasks}
            logger.info(f"Extracted {len(validated_tasks)} tasks from meeting")
            return result
            
        except Exception as e:
            logger.error(f"Error extracting tasks: {e}")
            raise
    
    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse JSON from LLM response, handling markdown code blocks.
        
        Args:
            response_text: Raw LLM response text
            
        Returns:
            Parsed JSON dictionary
        """
        # Handle empty or whitespace-only response
        if not response_text or not response_text.strip():
            logger.warning("LLM returned empty response, defaulting to empty tasks")
            return {"tasks": []}
        
        # Remove markdown code blocks if present
        cleaned_text = response_text
        
        if "```json" in cleaned_text:
            cleaned_text = cleaned_text.split("```json")[1]
            if "```" in cleaned_text:
                cleaned_text = cleaned_text.split("```")[0]
        elif "```" in cleaned_text:
            parts = cleaned_text.split("```")
            if len(parts) >= 2:
                cleaned_text = parts[1]
        
        cleaned_text = cleaned_text.strip()
        
        # Handle empty after cleanup
        if not cleaned_text:
            logger.warning("LLM response empty after cleanup, defaulting to empty tasks")
            return {"tasks": []}
        
        try:
            return json.loads(cleaned_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {e}")
            # Try to extract JSON object from text
            start_idx = cleaned_text.find("{")
            end_idx = cleaned_text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                try:
                    return json.loads(cleaned_text[start_idx:end_idx])
                except json.JSONDecodeError:
                    pass
            return {"tasks": []}
    
    def _extract_tasks_fallback(self, transcript: str) -> Dict[str, Any]:
        """
        Fallback task extraction using regex patterns when LLM returns non-JSON.
        
        Args:
            transcript: Meeting transcript text
            
        Returns:
            Dictionary with extracted tasks
        """
        import re
        
        tasks = []
        
        # Patterns that indicate task assignment
        patterns = [
            # "X will do Y" or "X should do Y"
            r"(\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?)\s+(?:will|should|needs?\s+to|is\s+going\s+to)\s+(.+?)(?:\.|$)",
            # "assigned to X" or "X is assigned"
            r"(.+?)\s+(?:is\s+)?assigned\s+to\s+(\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?)",
            # "X to start/begin/work on Y"
            r"(\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?)\s+to\s+(?:start|begin|work\s+on|handle)\s+(.+?)(?:\.|$)",
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, transcript, re.IGNORECASE)
            for match in matches:
                if len(match) == 2:
                    assignee, task_desc = match[0].strip(), match[1].strip()
                    # Swap if pattern matched in reverse order
                    if "assigned to" in pattern:
                        task_desc, assignee = assignee, task_desc
                    
                    if task_desc and len(task_desc) > 5:  # Skip very short matches
                        tasks.append({
                            "title": task_desc[:200],
                            "assignee": assignee,
                            "due_date": None
                        })
        
        if tasks:
            logger.info(f"Fallback extraction found {len(tasks)} tasks")
        
        return {"tasks": tasks}
    
    def analyze_meeting(self, transcript: str) -> Dict[str, Any]:
        """
        Perform complete meeting analysis: summarize and extract tasks.
        
        Args:
            transcript: Full meeting transcript
            
        Returns:
            Dictionary with summary and tasks
        """
        summary = self.summarize_meeting(transcript)
        tasks = self.extract_tasks(transcript, summary)
        
        return {
            "summary": summary,
            "tasks": tasks.get("tasks", [])
        }


# Singleton instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create the singleton LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client

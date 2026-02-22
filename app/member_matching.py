"""
Member name matching utilities with fuzzy matching, alias support, and initials expansion.
Handles variations like "V.S." → "Mukundan V S", "Kyla" → "Kailas S S".
"""
import logging
import re
from difflib import SequenceMatcher
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


# Team members for matching (can be overridden)
DEFAULT_TEAM_MEMBERS = [
    "Nikhil J Prasad",
    "Kailas S S",
    "S Govind Krishnan",
    "Mukundan V S"
]

# Alias dictionary for common variations
NAME_ALIASES: Dict[str, List[str]] = {
    "Nikhil J Prasad": ["nikhil", "nik", "nikhi", "n.j. prasad", "njp", "n j prasad"],
    "Kailas S S": ["kailas", "kaila", "kyla", "kyle", "kailash", "k.s.s", "kss", "k s s"],
    "S Govind Krishnan": ["govind", "govin", "govi", "krishnan", "s.govind", "s govind", "sgk"],
    "Mukundan V S": ["mukundan", "mukund", "muku", "vs", "v.s.", "v s", "mvs", "m.v.s"]
}

# Reverse alias lookup
_ALIAS_TO_MEMBER: Dict[str, str] = {}
for member, aliases in NAME_ALIASES.items():
    for alias in aliases:
        _ALIAS_TO_MEMBER[alias.lower()] = member


def normalize_name(name: str) -> str:
    """
    Normalize a name for comparison by:
    - Lowercasing
    - Removing punctuation
    - Normalizing whitespace
    - Expanding common abbreviations
    
    Args:
        name: Raw name string
        
    Returns:
        Normalized name string
    """
    if not name:
        return ""
    
    # Lowercase
    normalized = name.lower().strip()
    
    # Replace punctuation with spaces (V.S. → V S)
    normalized = re.sub(r'[.,;:\-_/\\]+', ' ', normalized)
    
    # Normalize multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Remove special characters but keep alphanumeric and spaces
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    
    return normalized.strip()


def expand_initials(name: str) -> List[str]:
    """
    Generate possible expansions for initials in a name.
    E.g., "V.S." could match "V S", "VS", "Venkat Suresh", etc.
    
    Args:
        name: Name that may contain initials
        
    Returns:
        List of possible expansions
    """
    expansions = [name]
    normalized = normalize_name(name)
    
    # Check if name looks like initials (1-3 single letters)
    parts = normalized.split()
    if all(len(part) == 1 for part in parts):
        # It's all initials - add concatenated version
        expansions.append(''.join(parts))
        expansions.append(' '.join(parts))
    
    return expansions


def calculate_similarity(name1: str, name2: str) -> float:
    """
    Calculate similarity between two names using multiple strategies.
    
    Args:
        name1: First name
        name2: Second name
        
    Returns:
        Similarity score between 0.0 and 1.0
    """
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    
    if not n1 or not n2:
        return 0.0
    
    # Exact match after normalization
    if n1 == n2:
        return 1.0
    
    # Check if one is contained in the other
    if n1 in n2 or n2 in n1:
        return 0.85
    
    # Base similarity using SequenceMatcher
    base_ratio = SequenceMatcher(None, n1, n2).ratio()
    
    # Part-by-part matching (for multi-word names)
    parts1 = set(n1.split())
    parts2 = set(n2.split())
    
    # Check for matching parts
    common_parts = parts1 & parts2
    if common_parts:
        part_bonus = len(common_parts) / max(len(parts1), len(parts2)) * 0.3
        base_ratio = max(base_ratio, 0.5 + part_bonus)
    
    # Individual part similarity
    max_part_ratio = 0.0
    for p1 in parts1:
        for p2 in parts2:
            if len(p1) > 1 and len(p2) > 1:  # Skip single letters
                part_ratio = SequenceMatcher(None, p1, p2).ratio()
                max_part_ratio = max(max_part_ratio, part_ratio)
    
    # Boost if a significant part matches
    if max_part_ratio > 0.8:
        base_ratio = max(base_ratio, max_part_ratio * 0.9)
    
    return base_ratio


def match_member_name(
    llm_name: str,
    members: Optional[List[str]] = None,
    threshold: float = 0.6
) -> Optional[Tuple[str, float]]:
    """
    Match an LLM-extracted name to the closest team member.
    
    Matching strategies (in order):
    1. Exact alias match
    2. Exact normalized match
    3. Substring match
    4. Fuzzy similarity match
    
    Args:
        llm_name: Name extracted by LLM
        members: List of team member names (defaults to DEFAULT_TEAM_MEMBERS)
        threshold: Minimum similarity score to accept a match
        
    Returns:
        Tuple of (matched_member_name, similarity_score) or None if no match
    """
    if not llm_name or llm_name.lower() in ['unassigned', 'none', 'null', 'n/a', '']:
        return None
    
    if members is None:
        members = DEFAULT_TEAM_MEMBERS
    
    normalized_input = normalize_name(llm_name)
    
    # Strategy 1: Check alias dictionary first
    if normalized_input in _ALIAS_TO_MEMBER:
        matched = _ALIAS_TO_MEMBER[normalized_input]
        logger.info(f"Alias match: '{llm_name}' -> '{matched}' (via alias)")
        return (matched, 1.0)
    
    best_match: Optional[str] = None
    best_score: float = 0.0
    
    for member in members:
        member_normalized = normalize_name(member)
        
        # Strategy 2: Exact normalized match
        if normalized_input == member_normalized:
            logger.info(f"Exact match: '{llm_name}' -> '{member}'")
            return (member, 1.0)
        
        # Check aliases for this member
        member_aliases = NAME_ALIASES.get(member, [])
        for alias in member_aliases:
            if normalized_input == normalize_name(alias):
                logger.info(f"Alias match: '{llm_name}' -> '{member}' (alias: {alias})")
                return (member, 1.0)
        
        # Strategy 3: Calculate similarity
        score = calculate_similarity(llm_name, member)
        
        # Also check against aliases
        for alias in member_aliases:
            alias_score = calculate_similarity(llm_name, alias)
            score = max(score, alias_score)
        
        if score > best_score:
            best_score = score
            best_match = member
    
    # Check if best match meets threshold
    if best_match and best_score >= threshold:
        logger.info(f"Fuzzy match: '{llm_name}' -> '{best_match}' (similarity: {best_score:.2f})")
        return (best_match, best_score)
    
    logger.warning(f"No member match found for '{llm_name}' (best: {best_match}, score: {best_score:.2f})")
    return None


def get_member_name(llm_name: str, members: Optional[List[str]] = None) -> Optional[str]:
    """
    Convenience function to get just the matched member name.
    
    Args:
        llm_name: Name extracted by LLM
        members: Optional list of team members
        
    Returns:
        Matched member name or None
    """
    result = match_member_name(llm_name, members)
    return result[0] if result else None


def add_alias(member_name: str, alias: str) -> None:
    """
    Add a new alias for a team member.
    
    Args:
        member_name: Full team member name
        alias: New alias to add
    """
    if member_name not in NAME_ALIASES:
        NAME_ALIASES[member_name] = []
    
    normalized_alias = alias.lower().strip()
    if normalized_alias not in NAME_ALIASES[member_name]:
        NAME_ALIASES[member_name].append(normalized_alias)
        _ALIAS_TO_MEMBER[normalized_alias] = member_name
        logger.info(f"Added alias '{alias}' for '{member_name}'")


def update_team_members(members: List[str]) -> None:
    """
    Update the default team members list.
    
    Args:
        members: New list of team member names
    """
    global DEFAULT_TEAM_MEMBERS
    DEFAULT_TEAM_MEMBERS = members.copy()
    logger.info(f"Updated team members: {members}")

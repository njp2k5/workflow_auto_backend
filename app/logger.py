"""
Custom logging configuration for workflow automation.
Provides clear, presentable logs especially for LangGraph orchestration,
showing node activations with visual formatting.
"""
import logging
import sys
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal formatting."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Text colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright colors
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    
    # Background colors
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"


# Node icons for LangGraph visualization
NODE_ICONS = {
    "summarize_meeting": "📝",
    "extract_tasks": "📋",
    "create_jira_issues": "🎫",
    "update_confluence_page": "📄",
    "store_results": "💾",
    "pipeline": "🔄",
    "watcher": "👁️",
    "transcriber": "🎙️",
    "llm": "🤖",
    "jira": "🎯",
    "confluence": "📖",
    "database": "🗄️",
    "default": "▶️",
}


class WorkflowFormatter(logging.Formatter):
    """
    Custom formatter that provides clean, presentable log output.
    Especially formatted for LangGraph orchestration visibility.
    """
    
    # Level-specific formatting
    LEVEL_FORMATS = {
        logging.DEBUG: (Colors.DIM, "DEBUG"),
        logging.INFO: (Colors.BRIGHT_CYAN, "INFO "),
        logging.WARNING: (Colors.BRIGHT_YELLOW, "WARN "),
        logging.ERROR: (Colors.BRIGHT_RED, "ERROR"),
        logging.CRITICAL: (Colors.BOLD + Colors.BRIGHT_RED, "CRIT "),
    }
    
    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        # Get level formatting
        color, level_text = self.LEVEL_FORMATS.get(
            record.levelno,
            (Colors.WHITE, record.levelname[:5])
        )
        
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        
        # Determine component/module name
        module = record.name.split(".")[-1] if record.name else "root"
        
        # Get icon for module
        icon = NODE_ICONS.get(module, NODE_ICONS.get("default", ""))
        
        # Build the formatted message
        if self.use_colors:
            # Colorized output
            level_str = f"{color}{level_text}{Colors.RESET}"
            time_str = f"{Colors.DIM}{timestamp}{Colors.RESET}"
            module_str = f"{Colors.BRIGHT_BLUE}{module:20}{Colors.RESET}"
            msg = record.getMessage()
            
            formatted = f"{time_str} │ {level_str} │ {icon} {module_str} │ {msg}"
        else:
            # Plain text output (for file logging)
            formatted = f"{timestamp} | {level_text} | {icon} {module:20} | {record.getMessage()}"
        
        # Add exception info if present
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        
        return formatted


class NodeLogFormatter(logging.Formatter):
    """
    Specialized formatter for LangGraph node execution logs.
    Provides clear visual indicators for node entry/exit and transitions.
    """
    
    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()
        
        if self.use_colors:
            return f"{Colors.DIM}{timestamp}{Colors.RESET} │ {msg}"
        return f"{timestamp} | {msg}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    use_colors: bool = True
) -> None:
    """
    Configure logging for the workflow automation system.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file
        use_colors: Whether to use ANSI colors in console output
    """
    # Get numeric level
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(WorkflowFormatter(use_colors=use_colors))
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(WorkflowFormatter(use_colors=False))
        root_logger.addHandler(file_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


# ============================================================================
# LangGraph Node Logging Utilities
# ============================================================================

def log_node_entry(node_name: str, logger: logging.Logger) -> None:
    """Log entry into a LangGraph node with visual formatting."""
    icon = NODE_ICONS.get(node_name, NODE_ICONS["default"])
    
    if sys.stdout.isatty():
        border = f"{Colors.BRIGHT_GREEN}{'─' * 60}{Colors.RESET}"
        header = f"{Colors.BOLD}{Colors.BRIGHT_GREEN}▶ ENTERING NODE: {node_name.upper()}{Colors.RESET}"
        logger.info(f"\n{border}")
        logger.info(f"{icon}  {header}")
        logger.info(border)
    else:
        logger.info(f"\n{'─' * 60}")
        logger.info(f"{icon}  ▶ ENTERING NODE: {node_name.upper()}")
        logger.info("─" * 60)


def log_node_exit(node_name: str, logger: logging.Logger, success: bool = True, duration_ms: Optional[float] = None) -> None:
    """Log exit from a LangGraph node with visual formatting."""
    icon = NODE_ICONS.get(node_name, NODE_ICONS["default"])
    status = "✓ COMPLETED" if success else "✗ FAILED"
    duration_str = f" ({duration_ms:.0f}ms)" if duration_ms else ""
    
    if sys.stdout.isatty():
        color = Colors.BRIGHT_GREEN if success else Colors.BRIGHT_RED
        border = f"{color}{'─' * 60}{Colors.RESET}"
        footer = f"{Colors.BOLD}{color}◀ {status}: {node_name.upper()}{duration_str}{Colors.RESET}"
        logger.info(f"{icon}  {footer}")
        logger.info(f"{border}\n")
    else:
        logger.info(f"{icon}  ◀ {status}: {node_name.upper()}{duration_str}")
        logger.info(f"{'─' * 60}\n")


def log_node_transition(from_node: str, to_node: str, logger: logging.Logger) -> None:
    """Log transition between LangGraph nodes."""
    from_icon = NODE_ICONS.get(from_node, "")
    to_icon = NODE_ICONS.get(to_node, "")
    
    if sys.stdout.isatty():
        arrow = f"{Colors.BRIGHT_MAGENTA}  ════▶  {Colors.RESET}"
        logger.info(f"{from_icon} {from_node}{arrow}{to_icon} {to_node}")
    else:
        logger.info(f"{from_icon} {from_node}  ══════▶  {to_icon} {to_node}")


def log_pipeline_start(pipeline_name: str, logger: logging.Logger, context: Optional[dict] = None) -> None:
    """Log the start of a pipeline execution."""
    icon = NODE_ICONS.get("pipeline", "🔄")
    
    if sys.stdout.isatty():
        border_char = "═"
        top_border = f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{border_char * 70}{Colors.RESET}"
        header = f"{Colors.BOLD}{Colors.BRIGHT_CYAN}║  {icon}  PIPELINE START: {pipeline_name.upper()}{Colors.RESET}"
        
        logger.info(f"\n{top_border}")
        logger.info(header)
        if context:
            for key, value in context.items():
                value_str = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                logger.info(f"{Colors.CYAN}║    {key}: {value_str}{Colors.RESET}")
        logger.info(f"{top_border}\n")
    else:
        logger.info(f"\n{'═' * 70}")
        logger.info(f"║  {icon}  PIPELINE START: {pipeline_name.upper()}")
        if context:
            for key, value in context.items():
                value_str = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                logger.info(f"║    {key}: {value_str}")
        logger.info(f"{'═' * 70}\n")


def log_pipeline_end(pipeline_name: str, logger: logging.Logger, success: bool = True, duration_ms: Optional[float] = None, results: Optional[dict] = None) -> None:
    """Log the end of a pipeline execution."""
    icon = NODE_ICONS.get("pipeline", "🔄")
    status = "✓ SUCCESS" if success else "✗ FAILED"
    status_icon = "✅" if success else "❌"
    duration_str = f" in {duration_ms:.0f}ms" if duration_ms else ""
    
    if sys.stdout.isatty():
        color = Colors.BRIGHT_GREEN if success else Colors.BRIGHT_RED
        border_char = "═"
        border = f"{Colors.BOLD}{color}{border_char * 70}{Colors.RESET}"
        footer = f"{Colors.BOLD}{color}║  {status_icon}  PIPELINE {status}: {pipeline_name.upper()}{duration_str}{Colors.RESET}"
        
        logger.info(f"\n{border}")
        if results:
            for key, value in results.items():
                value_str = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                logger.info(f"{color}║    {key}: {value_str}{Colors.RESET}")
        logger.info(footer)
        logger.info(f"{border}\n")
    else:
        logger.info(f"\n{'═' * 70}")
        if results:
            for key, value in results.items():
                value_str = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                logger.info(f"║    {key}: {value_str}")
        logger.info(f"║  {status_icon}  PIPELINE {status}: {pipeline_name.upper()}{duration_str}")
        logger.info(f"{'═' * 70}\n")


def log_step_progress(step_num: int, total_steps: int, step_name: str, logger: logging.Logger) -> None:
    """Log progress through pipeline steps."""
    progress_bar_len = 20
    filled = int((step_num / total_steps) * progress_bar_len)
    bar = "█" * filled + "░" * (progress_bar_len - filled)
    percentage = (step_num / total_steps) * 100
    
    if sys.stdout.isatty():
        logger.info(
            f"{Colors.BRIGHT_YELLOW}[{bar}] {percentage:.0f}% │ "
            f"Step {step_num}/{total_steps}: {step_name}{Colors.RESET}"
        )
    else:
        logger.info(f"[{bar}] {percentage:.0f}% | Step {step_num}/{total_steps}: {step_name}")


# ============================================================================
# Decorator for node logging
# ============================================================================

def langgraph_node(node_name: Optional[str] = None):
    """
    Decorator to automatically log LangGraph node entry and exit.
    
    Usage:
        @langgraph_node("summarize_meeting")
        def summarize_meeting(state: MeetingState) -> MeetingState:
            ...
    """
    def decorator(func: Callable) -> Callable:
        name = node_name or func.__name__
        node_logger = logging.getLogger(name)
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import time
            start_time = time.time()
            
            log_node_entry(name, node_logger)
            
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                log_node_exit(name, node_logger, success=True, duration_ms=duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                node_logger.error(f"Node error: {e}")
                log_node_exit(name, node_logger, success=False, duration_ms=duration_ms)
                raise
        
        return wrapper
    return decorator

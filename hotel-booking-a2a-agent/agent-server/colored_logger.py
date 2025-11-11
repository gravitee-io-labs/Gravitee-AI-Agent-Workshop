"""
Colored logging configuration for the Hotel Booking Agent system.
"""
import logging
import sys


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and component prefixes."""
    
    # Component colors
    AGENT_COLOR = '\033[38;2;167;199;231m'     # #A7C7E7
    LLM_COLOR = '\033[38;2;191;216;229m'       # #BFD8E5
    MCP_COLOR = '\033[38;2;207;232;243m'       # #CFE8F3
    
    # Text formatting
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Log level colors
    DEBUG_COLOR = '\033[90m'      # Gray
    INFO_COLOR = '\033[0m'        # Default
    WARNING_COLOR = '\033[93m'    # Yellow
    ERROR_COLOR = '\033[91m'      # Red
    CRITICAL_COLOR = '\033[95m'   # Magenta
    
    LEVEL_COLORS = {
        'DEBUG': DEBUG_COLOR,
        'INFO': INFO_COLOR,
        'WARNING': WARNING_COLOR,
        'ERROR': ERROR_COLOR,
        'CRITICAL': CRITICAL_COLOR,
    }
    
    def __init__(self, component_name: str, component_color: str):
        """
        Initialize the colored formatter.
        
        Args:
            component_name: Name of the component (e.g., "AI-AGENT", "LLM-CLIENT")
            component_color: ANSI color code for the component
        """
        super().__init__()
        self.component_name = component_name
        self.component_color = component_color
        
    def format(self, record):
        """Format the log record with colors."""
        # Get level color
        level_color = self.LEVEL_COLORS.get(record.levelname, self.INFO_COLOR)
        
        # Format component tag with component color
        component_tag = f"{self.component_color}{self.BOLD}[{self.component_name}]{self.RESET}"
        
        # Format level with level color
        level_tag = f"{level_color}{record.levelname}{self.RESET}"
        
        # Format timestamp
        timestamp = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        
        # Format message
        message = record.getMessage()
        
        # Build final log line
        log_line = f"{timestamp} {component_tag} {level_tag}: {message}"
        
        # Add exception info if present
        if record.exc_info:
            log_line += "\n" + self.formatException(record.exc_info)
        
        return log_line


def setup_logger(name: str, component_name: str, component_color: str, level=logging.INFO) -> logging.Logger:
    """
    Set up a logger with colored output.
    
    Args:
        name: Logger name (usually __name__)
        component_name: Display name for the component (e.g., "AI-AGENT")
        component_color: ANSI color code for the component
        level: Logging level
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    logger.setLevel(level)
    logger.propagate = False
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    
    # Set formatter
    formatter = ColoredFormatter(component_name, component_color)
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    
    return logger


def get_agent_logger(name: str) -> logging.Logger:
    """Get a logger configured for the AI Agent."""
    return setup_logger(
        name=name,
        component_name="AI-AGENT",
        component_color=ColoredFormatter.AGENT_COLOR
    )


def get_llm_logger(name: str) -> logging.Logger:
    """Get a logger configured for the LLM Client."""
    return setup_logger(
        name=name,
        component_name="LLM-CLIENT",
        component_color=ColoredFormatter.LLM_COLOR
    )


def get_mcp_logger(name: str) -> logging.Logger:
    """Get a logger configured for the MCP Client."""
    return setup_logger(
        name=name,
        component_name="MCP-CLIENT",
        component_color=ColoredFormatter.MCP_COLOR
    )

"""Colored logging configuration."""
import logging
import sys


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and component prefixes."""

    AGENT_COLOR = '\033[38;2;167;199;231m'
    LLM_COLOR = '\033[38;2;191;216;229m'
    MCP_COLOR = '\033[38;2;207;232;243m'

    RESET = '\033[0m'
    BOLD = '\033[1m'

    LEVEL_COLORS = {
        'DEBUG': '\033[90m',
        'INFO': '\033[0m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
    }

    def __init__(self, component_name: str, component_color: str):
        super().__init__()
        self.component_name = component_name
        self.component_color = component_color

    def format(self, record):
        level_color = self.LEVEL_COLORS.get(record.levelname, '\033[0m')
        component_tag = f"{self.component_color}{self.BOLD}[{self.component_name}]{self.RESET}"
        level_tag = f"{level_color}{record.levelname}{self.RESET}"
        timestamp = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        message = record.getMessage()
        log_line = f"{timestamp} {component_tag} {level_tag}: {message}"
        if record.exc_info:
            log_line += "\n" + self.formatException(record.exc_info)
        return log_line


def _setup_logger(name: str, component_name: str, component_color: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(ColoredFormatter(component_name, component_color))
    logger.addHandler(handler)
    return logger


def get_agent_logger(name: str) -> logging.Logger:
    return _setup_logger(name, "AGENT", ColoredFormatter.AGENT_COLOR)


def get_llm_logger(name: str) -> logging.Logger:
    return _setup_logger(name, "LLM", ColoredFormatter.LLM_COLOR)


def get_mcp_logger(name: str) -> logging.Logger:
    return _setup_logger(name, "MCP", ColoredFormatter.MCP_COLOR)

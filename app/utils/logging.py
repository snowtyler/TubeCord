"""
Structured logging configuration for the application.
"""

import logging
import sys
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Colored log formatter for console output."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record):
        """Format log record with colors."""
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        
        # Add color to level name
        record.levelname = f"{color}{record.levelname}{reset}"
        
        return super().format(record)


def setup_logging(log_level: str = 'INFO', use_colors: bool = True) -> None:
    """
    Set up structured logging for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        use_colors: Whether to use colored output for console logging
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    
    # Choose formatter based on color preference
    if use_colors and sys.stdout.isatty():
        formatter = ColoredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Set specific logger levels
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    
    logging.info(f"Logging initialized at level {log_level}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the specified name.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding contextual information to logs."""
    
    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = None
    
    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.context.items():
                setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)


# Convenience functions for common logging patterns
def log_websub_event(logger: logging.Logger, event_type: str, details: dict):
    """Log WebSub-related events with structured data."""
    with LogContext(logger, component='websub', event_type=event_type):
        logger.info(f"WebSub {event_type}: {details}")


def log_discord_event(logger: logging.Logger, event_type: str, webhook_url: str, success: bool, details: Optional[dict] = None):
    """Log Discord-related events with structured data."""
    with LogContext(logger, component='discord', webhook_url=webhook_url[:50] + '...'):
        status = 'SUCCESS' if success else 'FAILED'
        message = f"Discord {event_type} {status}"
        if details:
            message += f": {details}"
        
        if success:
            logger.info(message)
        else:
            logger.error(message)


def log_notification_processing(logger: logging.Logger, video_id: str, title: str, success: bool, error: Optional[str] = None):
    """Log notification processing events."""
    with LogContext(logger, component='notification', video_id=video_id):
        if success:
            logger.info(f"Successfully processed notification: {title}")
        else:
            logger.error(f"Failed to process notification: {title} - {error}")
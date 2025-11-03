"""
Shared utilities for Lambda functions.
"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone


def setup_logger(logger_name: str) -> logging.Logger:
    """
    Set up a logger with consistent formatting.
    
    Args:
        logger_name: Name of the logger
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


def get_env_var(key: str, default: Optional[str] = None) -> str:
    """
    Get environment variable with optional default.
    
    Args:
        key: Environment variable key
        default: Optional default value
        
    Returns:
        Environment variable value
        
    Raises:
        ValueError: If variable is not set and no default provided
    """
    value = os.environ.get(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value


def create_response(
    status_code: int,
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a standardized API Gateway response.
    
    Args:
        status_code: HTTP status code
        body: Response body dictionary
        headers: Optional custom headers
        
    Returns:
        API Gateway formatted response
    """
    default_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',  # Configure appropriately for production
    }
    
    if headers:
        default_headers.update(headers)
    
    return {
        'statusCode': status_code,
        'headers': default_headers,
        'body': body if isinstance(body, str) else json.dumps(body)
    }


def get_current_timestamp() -> int:
    """
    Get current UTC timestamp as integer.
    
    Returns:
        Current Unix timestamp
    """
    return int(datetime.now(timezone.utc).timestamp())


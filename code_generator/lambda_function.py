"""
Code Generator Lambda Function (Account A).

Generates KMS-signed codes with expiry using word-based encoding.
Uses daily counter + date + timestamp for uniqueness and TTL validation.
This Lambda runs in Account A (same account as authorizer function to share KMS key).
"""
import os
import json
import logging
from typing import Dict, Any
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from dictionary import encode_bits_to_words

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
kms_client = boto3.client('kms')

# Configuration
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'file-whitelist-codes')
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')
if not KMS_KEY_ID:
    raise ValueError("KMS_KEY_ID environment variable is required")

CODE_EXPIRY_HOURS = int(os.environ.get('CODE_EXPIRY_HOURS', '24'))


def get_utc_date_string() -> str:
    """Get UTC date string (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def build_counter_id() -> str:
    """Build counter ID for today: code-count-yyyy-mm-dd."""
    date_str = get_utc_date_string()
    return f"code-count-{date_str}"


def update_counter() -> int:
    """
    Increment daily counter in DynamoDB and return new count.
    
    Returns:
        New counter value
    """
    counter_id = build_counter_id()
    try:
        response = dynamodb.update_item(
            TableName=DYNAMODB_TABLE_NAME,
            Key={'counterId': {'S': counter_id}},
            UpdateExpression='SET #c = if_not_exists(#c, :start) + :inc',
            ExpressionAttributeNames={'#c': 'count'},
            ExpressionAttributeValues={
                ':inc': {'N': '1'},
                ':start': {'N': '0'}
            },
            ReturnValues='UPDATED_NEW'
        )
        new_count = int(response['Attributes']['count']['N'])
        return new_count
    except ClientError as e:
        logger.error(f"Error incrementing counter: {e}")
        raise


def get_current_hours() -> int:
    """Get current hours since epoch."""
    return int(datetime.now(timezone.utc).timestamp() / 3600)


def generate_mac(message: str) -> bytes:
    """
    Generate MAC using KMS HMAC_SHA_256.
    
    Args:
        message: Message to sign
        
    Returns:
        MAC bytes (first 96 bits used)
    """
    try:
        response = kms_client.generate_mac(
            KeyId=KMS_KEY_ID,
            Message=message.encode('utf-8'),
            MacAlgorithm='HMAC_SHA_256'
        )
        if not response.get('Mac'):
            raise ValueError("KMS did not return a MAC")
        return response['Mac']
    except ClientError as e:
        logger.error(f"KMS MAC generation failed: {e}")
        raise


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body)
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for code generation."""
    try:
        logger.info("Code generator Lambda invoked")
        
        # Update counter
        key_id = update_counter()
        if key_id < 0 or key_id > 1023:
            return create_response(400, {'error': 'Invalid keyId generated'})
        
        # Build message: counter (10 bits) | date | hours since epoch
        key_id_bits = f"{key_id:010b}"
        date = get_utc_date_string()
        hours = get_current_hours()
        message = f"{key_id_bits}|{date}|{hours}"
        
        # Generate MAC
        mac_result = generate_mac(message)
        
        # Use first 96 bits (12 bytes) of MAC
        mac_bytes = mac_result[:12]
        mac_bits = ''.join(f"{b:08b}" for b in mac_bytes)[:90]  # 90 bits
        
        # Combine: keyId (10 bits) + mac (90 bits) = 100 bits
        full_bits = key_id_bits + mac_bits
        
        # Encode as words
        words = encode_bits_to_words(full_bits)
        words_string = ' '.join(words)
        
        logger.info(f"Code generated: keyId={key_id}, words={words_string[:50]}...")
        
        return create_response(200, {
            'words': words_string,
            'expires_in_hours': CODE_EXPIRY_HOURS
        })
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': 'Failed to generate code'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})

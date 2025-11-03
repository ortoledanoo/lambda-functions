"""
Authorizer Lambda Function (Account B).

Validates codes using pure cryptographic validation (no DynamoDB).
Decodes words, validates MAC, and checks TTL.
This Lambda runs in Account B (same account as presign URL function).
"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from dictionary import decode_words_to_bits

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Initialize AWS clients
kms_client = boto3.client('kms')

# Configuration
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')
if not KMS_KEY_ID:
    raise ValueError("KMS_KEY_ID environment variable is required")

CODE_EXPIRY_HOURS = int(os.environ.get('CODE_EXPIRY_HOURS', '24'))
API_GW_ARN = os.environ.get('API_GW_ARN', '*')


def get_utc_date_string() -> str:
    """Get UTC date string (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def get_current_hours() -> int:
    """Get current hours since epoch."""
    return int(datetime.now(timezone.utc).timestamp() / 3600)


def generate_mac(message: str) -> bytes:
    """Generate MAC using KMS HMAC_SHA_256."""
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


def generate_policy(principal_id: str, effect: str, resource: str) -> Dict[str, Any]:
    """Generate IAM policy for API Gateway Lambda Authorizer."""
    return {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource
            }]
        }
    }


def validate_code(words_string: str) -> tuple[bool, Optional[str], Optional[int]]:
    """
    Validate code by decoding words, regenerating MAC, and checking TTL.
    
    Returns:
        Tuple of (is_valid, error_message, key_id)
    """
    try:
        # Parse words
        words = words_string.strip().split()
        if len(words) != 10:
            return False, "Invalid code format", None
        
        # Decode to bits
        bits = decode_words_to_bits(words)
        key_id_bits = bits[:10]
        mac_bits = bits[10:]
        
        # Extract key ID
        key_id = int(key_id_bits, 2)
        
        # Get current date and hours
        current_date = get_utc_date_string()
        current_hours = get_current_hours()
        
        # Try validating with hours within TTL window
        # Check hours from (current - TTL) to current + 1 (for clock skew tolerance)
        matches = False
        matched_offset = None
        
        # Try hours within TTL window (including tolerance for clock skew)
        for offset in range(-1, CODE_EXPIRY_HOURS + 1):  # -1 for clock skew tolerance
            try_hours = current_hours - offset
            try_date = current_date
            
            # Handle date rollover if trying previous day
            if offset > 0 and try_hours < 0:
                from datetime import timedelta
                prev_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
                try_date = prev_date
                # Calculate hours for previous day (simplified)
                try_hours = 24 + (try_hours % 24)
            
            message = f"{key_id_bits}|{try_date}|{try_hours}"
            
            # Generate MAC for this message
            mac_result = generate_mac(message)
            mac_bytes = mac_result[:12]
            generated_mac_bits = ''.join(f"{b:08b}" for b in mac_bytes)[:90]
            
            if generated_mac_bits == mac_bits:
                matches = True
                matched_offset = offset
                break
        
        if not matches:
            return False, "Invalid code signature", None
        
        # Check TTL: if offset > CODE_EXPIRY_HOURS, code is expired
        if matched_offset is not None and matched_offset > CODE_EXPIRY_HOURS:
            return False, f"Code expired ({matched_offset} hours old)", None
        
        return True, None, key_id
        
    except ValueError as e:
        return False, str(e), None
    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        return False, "Validation failed", None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for code authorization."""
    try:
        logger.info("Authorizer Lambda invoked")
        
        # Check if this is an API Gateway authorizer request
        is_authorizer = event.get('type') == 'TOKEN'
        
        if is_authorizer:
            # API Gateway Lambda Authorizer mode
            # Get words from Authorization header
            auth_token = event.get('authorizationToken', '')
            method_arn = event.get('methodArn', API_GW_ARN)
            
            # Extract words (format: "word0001 word0002 ..." or header name)
            words_string = auth_token.strip()
            
            if not words_string:
                return generate_policy('', 'Deny', method_arn)
            
            is_valid, error_msg, key_id = validate_code(words_string)
            
            if is_valid:
                logger.info(f"Authorization successful for keyId: {key_id}")
                return generate_policy(str(key_id), 'Allow', method_arn)
            else:
                logger.warning(f"Authorization failed: {error_msg}")
                return generate_policy('', 'Deny', method_arn)
        else:
            # Standalone validation endpoint mode
            # Get words from body or header
            body = {}
            if 'body' in event:
                body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            
            words_string = (
                body.get('words') or 
                event.get('headers', {}).get('x-authorization-words') or
                event.get('headers', {}).get('X-Authorization-Words') or
                event.get('queryStringParameters', {}).get('words')
            )
            
            if not words_string:
                return create_response(400, {'error': 'Missing words parameter'})
            
            is_valid, error_msg, key_id = validate_code(words_string)
            
            if is_valid:
                return create_response(200, {
                    'valid': True,
                    'message': 'Code is valid',
                    'key_id': key_id
                })
            else:
                return create_response(401, {
                    'valid': False,
                    'error': error_msg or 'Code validation failed'
                })
                
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        if is_authorizer:
            return generate_policy('', 'Deny', event.get('methodArn', '*'))
        return create_response(500, {'error': 'Internal server error'})

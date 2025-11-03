"""
Code Generator Lambda Function.

Generates HMAC or KMS-signed codes with expiry and stores metadata in DynamoDB.
"""
import os
import json
import secrets
import hashlib
import hmac
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

from shared.utils import setup_logger, get_env_var, create_response, get_current_timestamp

# Initialize logger
logger = setup_logger(__name__)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
kms_client = boto3.client('kms')

# Configuration from environment variables
DYNAMODB_TABLE_NAME = get_env_var('DYNAMODB_TABLE_NAME')
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')  # Optional - for KMS signing
CODE_EXPIRY_MINUTES = int(os.environ.get('CODE_EXPIRY_MINUTES', '60'))
USE_KMS = os.environ.get('USE_KMS', 'false').lower() == 'true'
HMAC_SECRET = os.environ.get('HMAC_SECRET')  # Required if not using KMS


def generate_nonce() -> str:
    """
    Generate a cryptographically secure random nonce.
    
    Returns:
        Random nonce string
    """
    return secrets.token_urlsafe(32)


def sign_with_hmac(code: str, secret: str) -> str:
    """
    Sign a code using HMAC-SHA256.
    
    Args:
        code: Code to sign
        secret: HMAC secret key
        
    Returns:
        HMAC signature as hex string
    """
    signature = hmac.new(
        secret.encode('utf-8'),
        code.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def sign_with_kms(code: str, key_id: str) -> str:
    """
    Sign a code using AWS KMS.
    
    Args:
        code: Code to sign
        key_id: KMS key ID or alias
        
    Returns:
        KMS signature as base64-encoded string
        
    Raises:
        ClientError: If KMS operation fails
    """
    try:
        response = kms_client.sign(
            KeyId=key_id,
            Message=code.encode('utf-8'),
            MessageType='RAW',
            SigningAlgorithm='RSASSA_PSS_SHA_256'
        )
        import base64
        return base64.b64encode(response['Signature']).decode('utf-8')
    except ClientError as e:
        logger.error(f"KMS signing failed: {e}")
        raise


def generate_code(nonce: str, timestamp: int) -> str:
    """
    Generate a code from nonce and timestamp.
    
    Args:
        nonce: Random nonce
        timestamp: Unix timestamp
        
    Returns:
        Generated code string
    """
    # Combine nonce and timestamp to create unique code
    code_data = f"{nonce}:{timestamp}"
    return hashlib.sha256(code_data.encode('utf-8')).hexdigest()[:32]  # 32 char hex


def store_code_metadata(
    code: str,
    signature: str,
    expires_at: int,
    nonce: str
) -> None:
    """
    Store code metadata in DynamoDB.
    
    Args:
        code: The generated code
        signature: HMAC or KMS signature
        expires_at: Expiration timestamp
        nonce: Random nonce
        
    Raises:
        ClientError: If DynamoDB operation fails
    """
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    
    try:
        table.put_item(
            Item={
                'code': code,  # Partition key
                'signature': signature,
                'expires_at': expires_at,
                'nonce': nonce,
                'created_at': get_current_timestamp(),
                'state': 'active',
                'ttl': expires_at  # DynamoDB TTL attribute for automatic cleanup
            },
            ConditionExpression='attribute_not_exists(code)'  # Prevent duplicates
        )
        logger.info(f"Stored code metadata for code: {code[:8]}...")
    except ClientError as e:
        logger.error(f"Failed to store code metadata: {e}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for code generation.
    
    Expected event structure:
    {
        "body": "{}"  # Optional JSON body
    }
    
    Returns:
        API Gateway response with generated code
    """
    try:
        logger.info("Code generator Lambda invoked")
        
        # Validate configuration
        if not USE_KMS and not HMAC_SECRET:
            logger.error("Either USE_KMS must be true or HMAC_SECRET must be set")
            return create_response(
                500,
                {'error': 'Configuration error: signing method not properly configured'}
            )
        
        # Generate code components
        nonce = generate_nonce()
        timestamp = get_current_timestamp()
        code = generate_code(nonce, timestamp)
        
        # Calculate expiry
        expires_at = timestamp + (CODE_EXPIRY_MINUTES * 60)
        
        # Sign the code
        if USE_KMS and KMS_KEY_ID:
            signature = sign_with_kms(code, KMS_KEY_ID)
            signing_method = 'KMS'
        else:
            signature = sign_with_hmac(code, HMAC_SECRET)
            signing_method = 'HMAC'
        
        # Store metadata in DynamoDB
        store_code_metadata(code, signature, expires_at, nonce)
        
        # Prepare response
        response_body = {
            'code': code,
            'signature': signature,
            'expires_at': expires_at,
            'expires_in_minutes': CODE_EXPIRY_MINUTES,
            'signing_method': signing_method
        }
        
        logger.info(f"Code generated successfully: {code[:8]}...")
        return create_response(200, response_body)
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': 'Failed to generate code'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})



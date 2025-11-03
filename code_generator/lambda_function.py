"""
Code Generator Lambda Function (Account A).

Generates HMAC or KMS-signed codes with expiry and stores metadata in DynamoDB.
This Lambda runs in Account A (separate from authorizer/presign functions).
"""
import os
import json
import logging
import secrets
import hashlib
import hmac
import base64
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
kms_client = boto3.client('kms')

# Configuration from environment variables
def get_env_var(key: str, default: Optional[str] = None) -> str:
    """Get environment variable with optional default."""
    value = os.environ.get(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value

DYNAMODB_TABLE_NAME = get_env_var('DYNAMODB_TABLE_NAME')
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')
CODE_EXPIRY_MINUTES = int(os.environ.get('CODE_EXPIRY_MINUTES', '60'))
USE_KMS = os.environ.get('USE_KMS', 'false').lower() == 'true'
HMAC_SECRET = os.environ.get('HMAC_SECRET')


def get_current_timestamp() -> int:
    """Get current UTC timestamp as integer."""
    return int(datetime.now(timezone.utc).timestamp())


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


def generate_nonce() -> str:
    """Generate a cryptographically secure random nonce."""
    return secrets.token_urlsafe(32)


def sign_with_hmac(code: str, secret: str) -> str:
    """Sign a code using HMAC-SHA256."""
    signature = hmac.new(
        secret.encode('utf-8'),
        code.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def sign_with_kms(code: str, key_id: str) -> str:
    """Sign a code using AWS KMS."""
    try:
        response = kms_client.sign(
            KeyId=key_id,
            Message=code.encode('utf-8'),
            MessageType='RAW',
            SigningAlgorithm='RSASSA_PSS_SHA_256'
        )
        return base64.b64encode(response['Signature']).decode('utf-8')
    except ClientError as e:
        logger.error(f"KMS signing failed: {e}")
        raise


def generate_code(nonce: str, timestamp: int) -> str:
    """Generate a code from nonce and timestamp."""
    code_data = f"{nonce}:{timestamp}"
    return hashlib.sha256(code_data.encode('utf-8')).hexdigest()[:32]


def store_code_metadata(code: str, signature: str, expires_at: int, nonce: str) -> None:
    """Store code metadata in DynamoDB."""
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    try:
        table.put_item(
            Item={
                'code': code,
                'signature': signature,
                'expires_at': expires_at,
                'nonce': nonce,
                'created_at': get_current_timestamp(),
                'state': 'active',
                'ttl': expires_at
            },
            ConditionExpression='attribute_not_exists(code)'
        )
        logger.info(f"Stored code metadata for code: {code[:8]}...")
    except ClientError as e:
        logger.error(f"Failed to store code metadata: {e}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for code generation."""
    try:
        logger.info("Code generator Lambda invoked")
        
        if not USE_KMS and not HMAC_SECRET:
            logger.error("Either USE_KMS must be true or HMAC_SECRET must be set")
            return create_response(500, {'error': 'Configuration error'})
        
        # Generate code
        nonce = generate_nonce()
        timestamp = get_current_timestamp()
        code = generate_code(nonce, timestamp)
        expires_at = timestamp + (CODE_EXPIRY_MINUTES * 60)
        
        # Sign the code
        if USE_KMS and KMS_KEY_ID:
            signature = sign_with_kms(code, KMS_KEY_ID)
            signing_method = 'KMS'
        else:
            signature = sign_with_hmac(code, HMAC_SECRET)
            signing_method = 'HMAC'
        
        # Store in DynamoDB
        store_code_metadata(code, signature, expires_at, nonce)
        
        response_body = {
            'code': code,
            'signature': signature,
            'expires_at': expires_at,
            'expires_in_minutes': CODE_EXPIRY_MINUTES,
            'signing_method': signing_method
        }
        
        logger.info(f"Code generated: {code[:8]}...")
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


"""
Authorizer Lambda Function (Account B).

Validates incoming codes by verifying HMAC/KMS signatures and checking TTL/state in DynamoDB.
This Lambda runs in Account B (same account as presign URL function).
"""
import os
import json
import logging
import hashlib
import hmac
import base64
from typing import Dict, Any, Optional, Tuple
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

# Configuration
def get_env_var(key: str, default: Optional[str] = None) -> str:
    """Get environment variable with optional default."""
    value = os.environ.get(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value

DYNAMODB_TABLE_NAME = get_env_var('DYNAMODB_TABLE_NAME')
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')
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


def verify_hmac_signature(code: str, signature: str, secret: str) -> bool:
    """Verify HMAC signature for a code."""
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        code.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


def verify_kms_signature(code: str, signature: str, key_id: str) -> bool:
    """Verify KMS signature for a code."""
    try:
        signature_bytes = base64.b64decode(signature)
        kms_client.verify(
            KeyId=key_id,
            Message=code.encode('utf-8'),
            MessageType='RAW',
            Signature=signature_bytes,
            SigningAlgorithm='RSASSA_PSS_SHA_256'
        )
        return True
    except (ClientError, Exception) as e:
        logger.warning(f"KMS signature verification failed: {e}")
        return False


def get_code_metadata(code: str) -> Optional[Dict[str, Any]]:
    """Retrieve code metadata from DynamoDB."""
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    try:
        response = table.get_item(
            Key={'code': code},
            ConsistentRead=True
        )
        return response.get('Item')
    except ClientError as e:
        logger.error(f"Failed to retrieve code metadata: {e}")
        raise


def validate_code(code: str, signature: str) -> Tuple[bool, Optional[str]]:
    """Validate a code by checking signature and DynamoDB state."""
    if not USE_KMS and not HMAC_SECRET:
        logger.error("Either USE_KMS must be true or HMAC_SECRET must be set")
        return False, "Configuration error"
    
    try:
        metadata = get_code_metadata(code)
        if not metadata:
            return False, "Invalid code"
        
        if metadata.get('state') != 'active':
            return False, "Code is not active"
        
        current_time = get_current_timestamp()
        expires_at = metadata.get('expires_at', 0)
        if current_time > expires_at:
            return False, "Code has expired"
        
        stored_signature = metadata.get('signature')
        if stored_signature != signature:
            return False, "Invalid signature"
        
        if USE_KMS and KMS_KEY_ID:
            is_valid = verify_kms_signature(code, signature, KMS_KEY_ID)
        else:
            is_valid = verify_hmac_signature(code, signature, HMAC_SECRET)
        
        if not is_valid:
            return False, "Signature verification failed"
        
        return True, None
        
    except ClientError as e:
        logger.error(f"DynamoDB error: {e}")
        return False, "Database error"


def generate_policy(principal_id: str, resource: str, effect: str = 'Allow') -> Dict[str, Any]:
    """Generate an IAM policy for API Gateway Lambda Authorizer."""
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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for code authorization."""
    try:
        logger.info("Authorizer Lambda invoked")
        
        # Check if this is an API Gateway authorizer request
        is_authorizer = event.get('type') == 'TOKEN'
        
        if is_authorizer:
            # API Gateway Lambda Authorizer mode
            token = event.get('authorizationToken', '')
            method_arn = event.get('methodArn', '')
            
            try:
                code, signature = token.split(':', 1)
            except ValueError:
                logger.warning("Invalid token format")
                return generate_policy('user', method_arn, 'Deny')
            
            is_valid, error_msg = validate_code(code, signature)
            
            if is_valid:
                logger.info(f"Authorization successful for code: {code[:8]}...")
                return generate_policy(code, method_arn, 'Allow')
            else:
                logger.warning(f"Authorization failed: {error_msg}")
                return generate_policy('user', method_arn, 'Deny')
        else:
            # Standalone validation endpoint mode
            body = {}
            if 'body' in event:
                body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            else:
                body = event.get('queryStringParameters') or {}
            
            code = body.get('code') or event.get('code')
            signature = body.get('signature') or event.get('signature')
            
            if not code or not signature:
                return create_response(400, {'error': 'Missing required parameters: code and signature'})
            
            is_valid, error_msg = validate_code(code, signature)
            
            if is_valid:
                return create_response(200, {'valid': True, 'message': 'Code is valid'})
            else:
                return create_response(401, {'valid': False, 'error': error_msg or 'Code validation failed'})
                
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': 'Database error'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})


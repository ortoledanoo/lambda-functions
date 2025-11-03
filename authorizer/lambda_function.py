"""
Authorizer Lambda Function.

Validates incoming codes by verifying HMAC/KMS signatures and checking TTL/state in DynamoDB.
Can be used as API Gateway Lambda Authorizer or standalone validation endpoint.
"""
import os
import json
import hashlib
import hmac
import base64
from typing import Dict, Any, Optional, Tuple

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
KMS_KEY_ID = os.environ.get('KMS_KEY_ID')  # Optional - for KMS verification
USE_KMS = os.environ.get('USE_KMS', 'false').lower() == 'true'
HMAC_SECRET = os.environ.get('HMAC_SECRET')  # Required if not using KMS


def verify_hmac_signature(code: str, signature: str, secret: str) -> bool:
    """
    Verify HMAC signature for a code.
    
    Args:
        code: Code to verify
        signature: Expected signature (hex string)
        secret: HMAC secret key
        
    Returns:
        True if signature is valid, False otherwise
    """
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        code.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature)


def verify_kms_signature(code: str, signature: str, key_id: str) -> bool:
    """
    Verify KMS signature for a code.
    
    Args:
        code: Code to verify
        signature: Base64-encoded signature
        key_id: KMS key ID or alias
        
    Returns:
        True if signature is valid, False otherwise
    """
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
    except ClientError as e:
        logger.warning(f"KMS signature verification failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in KMS verification: {e}")
        return False


def get_code_metadata(code: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve code metadata from DynamoDB.
    
    Args:
        code: Code to look up
        
    Returns:
        Code metadata dictionary or None if not found
    """
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    
    try:
        response = table.get_item(
            Key={'code': code},
            ConsistentRead=True  # Ensure we get latest data
        )
        
        if 'Item' in response:
            return response['Item']
        return None
    except ClientError as e:
        logger.error(f"Failed to retrieve code metadata: {e}")
        raise


def validate_code(code: str, signature: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a code by checking signature and DynamoDB state.
    
    Args:
        code: Code to validate
        signature: Signature to verify
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Validate configuration
    if not USE_KMS and not HMAC_SECRET:
        logger.error("Either USE_KMS must be true or HMAC_SECRET must be set")
        return False, "Configuration error"
    
    # Retrieve metadata from DynamoDB
    try:
        metadata = get_code_metadata(code)
        if not metadata:
            logger.warning(f"Code not found in database: {code[:8]}...")
            return False, "Invalid code"
    except ClientError as e:
        logger.error(f"DynamoDB error: {e}")
        return False, "Database error"
    
    # Check if code is active
    if metadata.get('state') != 'active':
        logger.warning(f"Code is not active: {code[:8]}...")
        return False, "Code is not active"
    
    # Check expiration
    current_time = get_current_timestamp()
    expires_at = metadata.get('expires_at', 0)
    if current_time > expires_at:
        logger.warning(f"Code has expired: {code[:8]}...")
        return False, "Code has expired"
    
    # Verify signature
    stored_signature = metadata.get('signature')
    if stored_signature != signature:
        logger.warning(f"Signature mismatch for code: {code[:8]}...")
        return False, "Invalid signature"
    
    # Verify signature cryptographically
    if USE_KMS and KMS_KEY_ID:
        is_valid = verify_kms_signature(code, signature, KMS_KEY_ID)
    else:
        is_valid = verify_hmac_signature(code, signature, HMAC_SECRET)
    
    if not is_valid:
        logger.warning(f"Signature verification failed for code: {code[:8]}...")
        return False, "Signature verification failed"
    
    return True, None


def generate_policy(principal_id: str, resource: str, effect: str = 'Allow') -> Dict[str, Any]:
    """
    Generate an IAM policy for API Gateway Lambda Authorizer.
    
    Args:
        principal_id: Principal identifier (e.g., user ID or code)
        resource: API Gateway resource ARN
        effect: Policy effect ('Allow' or 'Deny')
        
    Returns:
        IAM policy document
    """
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        }
    }
    return policy


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for code authorization.
    
    Can work in two modes:
    1. API Gateway Lambda Authorizer: expects 'authorizationToken' in headers
    2. Standalone validation endpoint: expects code and signature in request body
    
    Returns:
        API Gateway response or authorizer policy
    """
    try:
        logger.info("Authorizer Lambda invoked")
        
        # Determine if this is an API Gateway authorizer request
        is_authorizer = 'type' in event and event.get('type') == 'TOKEN'
        
        if is_authorizer:
            # API Gateway Lambda Authorizer mode
            token = event.get('authorizationToken', '')
            method_arn = event.get('methodArn', '')
            
            # Parse token (format: "code:signature")
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
            # Parse request body
            body = {}
            if 'body' in event:
                if isinstance(event['body'], str):
                    body = json.loads(event['body'])
                else:
                    body = event['body']
            else:
                # Try query parameters
                body = event.get('queryStringParameters') or {}
            
            code = body.get('code') or event.get('code')
            signature = body.get('signature') or event.get('signature')
            
            if not code or not signature:
                return create_response(
                    400,
                    {'error': 'Missing required parameters: code and signature'}
                )
            
            is_valid, error_msg = validate_code(code, signature)
            
            if is_valid:
                response_body = {
                    'valid': True,
                    'message': 'Code is valid'
                }
                return create_response(200, response_body)
            else:
                response_body = {
                    'valid': False,
                    'error': error_msg or 'Code validation failed'
                }
                return create_response(401, response_body)
                
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': 'Database error'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})



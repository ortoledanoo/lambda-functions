"""
Presign URL Lambda Function (Account B).

Generates presigned S3 URLs for authorized file uploads.
This Lambda runs in Account B (same account as authorizer function).
"""
import os
import json
import logging
import time
from typing import Dict, Any, Optional

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

# Initialize S3 client
s3_client = boto3.client('s3')

# Configuration
def get_env_var(key: str, default: Optional[str] = None) -> str:
    """Get environment variable with optional default."""
    value = os.environ.get(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value

S3_BUCKET_NAME = get_env_var('S3_BUCKET_NAME')
PRESIGNED_URL_EXPIRY_SECONDS = int(os.environ.get('PRESIGNED_URL_EXPIRY_SECONDS', '3600'))
ALLOWED_CONTENT_TYPES = os.environ.get('ALLOWED_CONTENT_TYPES', '*/*').split(',')
MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '100'))


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


def generate_presigned_url(code: str, filename: Optional[str] = None, content_type: Optional[str] = None) -> Dict[str, Any]:
    """Generate a presigned S3 URL for file upload."""
    timestamp = int(time.time())
    
    if filename:
        safe_filename = os.path.basename(filename).replace('..', '').replace('/', '')
        s3_key = f"uploads/{code}/{timestamp}-{safe_filename}"
    else:
        s3_key = f"uploads/{code}/{timestamp}"
    
    try:
        conditions = []
        fields = {}
        
        if content_type:
            conditions.append(['eq', '$Content-Type', content_type])
            fields = {'Content-Type': content_type}
        
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        conditions.append(['content-length-range', 0, max_bytes])
        
        response = s3_client.generate_presigned_post(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS
        )
        
        logger.info(f"Generated presigned URL for code: {code[:8]}..., key: {s3_key}")
        return response
        
    except ClientError as e:
        logger.error(f"S3 presigned URL generation failed: {e}")
        raise


def validate_content_type(content_type: Optional[str]) -> bool:
    """Validate that content type is allowed."""
    if '*' in ALLOWED_CONTENT_TYPES:
        return True
    if not content_type:
        return False
    
    for allowed in ALLOWED_CONTENT_TYPES:
        allowed = allowed.strip()
        if allowed.endswith('/*'):
            base_type = allowed.split('/')[0]
            if content_type.startswith(f"{base_type}/"):
                return True
        elif allowed == content_type:
            return True
    
    return False


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for presigned URL generation."""
    try:
        logger.info("Presign URL Lambda invoked")
        
        # Extract code from authorizer context
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        code = authorizer_context.get('principalId')
        
        # If not in authorizer context, try body/query params
        if not code:
            body = {}
            if 'body' in event:
                body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            code = body.get('code') or event.get('queryStringParameters', {}).get('code')
            
            if not code:
                return create_response(400, {'error': 'Missing authorization code'})
        
        # Parse request body
        body = {}
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
        filename = body.get('filename')
        content_type = body.get('content_type') or body.get('contentType')
        
        # Validate content type
        if content_type and not validate_content_type(content_type):
            return create_response(400, {'error': f'Content type not allowed: {content_type}'})
        
        # Generate presigned URL
        presigned_data = generate_presigned_url(code=code, filename=filename, content_type=content_type)
        
        response_body = {
            'presigned_url': presigned_data['url'],
            'fields': presigned_data['fields'],
            'method': 'POST',
            'expires_in_seconds': PRESIGNED_URL_EXPIRY_SECONDS,
            'max_file_size_mb': MAX_FILE_SIZE_MB,
            'bucket': S3_BUCKET_NAME
        }
        
        logger.info(f"Presigned URL generated for code: {code[:8]}...")
        return create_response(200, response_body)
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': 'Failed to generate presigned URL'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})


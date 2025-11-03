"""
Presign URL Lambda Function.

Generates presigned S3 URLs for authorized file uploads.
"""
import os
import json
import time
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

from shared.utils import setup_logger, get_env_var, create_response

# Initialize logger
logger = setup_logger(__name__)

# Initialize S3 client
s3_client = boto3.client('s3')

# Configuration from environment variables
S3_BUCKET_NAME = get_env_var('S3_BUCKET_NAME')
PRESIGNED_URL_EXPIRY_SECONDS = int(os.environ.get('PRESIGNED_URL_EXPIRY_SECONDS', '3600'))  # Default: 1 hour
ALLOWED_CONTENT_TYPES = os.environ.get('ALLOWED_CONTENT_TYPES', '*/*').split(',')
MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '100'))  # Default: 100MB


def generate_presigned_url(
    code: str,
    filename: Optional[str] = None,
    content_type: Optional[str] = None
) -> str:
    """
    Generate a presigned S3 URL for file upload.
    
    Args:
        code: Authorized code (used as prefix in S3 key)
        filename: Optional filename
        content_type: Optional content type for the upload
        
    Returns:
        Presigned URL string
        
    Raises:
        ClientError: If S3 operation fails
    """
    # Construct S3 key with code prefix for organization
    timestamp = int(time.time())
    
    if filename:
        # Sanitize filename to prevent path traversal
        safe_filename = os.path.basename(filename).replace('..', '').replace('/', '')
        s3_key = f"uploads/{code}/{timestamp}-{safe_filename}"
    else:
        s3_key = f"uploads/{code}/{timestamp}"
    
    # Generate presigned POST URL (allows more control than PUT)
    try:
        conditions = []
        
        # Add content type condition if specified
        if content_type:
            conditions.append(['eq', '$Content-Type', content_type])
            # Also add to fields for POST
            fields = {'Content-Type': content_type}
        else:
            fields = {}
        
        # Add file size limit condition
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
    """
    Validate that content type is allowed.
    
    Args:
        content_type: Content type to validate
        
    Returns:
        True if allowed or wildcard is set, False otherwise
    """
    if '*' in ALLOWED_CONTENT_TYPES:
        return True
    
    if not content_type:
        return False
    
    # Check if content type matches any allowed pattern
    for allowed in ALLOWED_CONTENT_TYPES:
        allowed = allowed.strip()
        if allowed.endswith('/*'):
            # Wildcard match for type
            base_type = allowed.split('/')[0]
            if content_type.startswith(f"{base_type}/"):
                return True
        elif allowed == content_type:
            return True
    
    return False


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for presigned URL generation.
    
    Expected event structure (after authorization):
    {
        "requestContext": {
            "authorizer": {
                "principalId": "code"  # From authorizer Lambda
            }
        },
        "body": {
            "filename": "example.pdf",  # Optional
            "content_type": "application/pdf"  # Optional
        }
    }
    
    Returns:
        API Gateway response with presigned URL
    """
    try:
        logger.info("Presign URL Lambda invoked")
        
        # Extract code from authorizer context (set by API Gateway after authorization)
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        code = authorizer_context.get('principalId')
        
        # If not in authorizer context, try to extract from body/query params
        if not code:
            body = {}
            if 'body' in event:
                if isinstance(event['body'], str):
                    body = json.loads(event['body'])
                else:
                    body = event['body']
            
            code = body.get('code') or event.get('queryStringParameters', {}).get('code')
            
            if not code:
                return create_response(
                    400,
                    {'error': 'Missing authorization code'}
                )
        
        # Parse request body for optional parameters
        body = {}
        if 'body' in event:
            if isinstance(event['body'], str):
                body = json.loads(event['body'])
            else:
                body = event['body']
        
        filename = body.get('filename')
        content_type = body.get('content_type') or body.get('contentType')
        
        # Validate content type if provided
        if content_type and not validate_content_type(content_type):
            return create_response(
                400,
                {'error': f'Content type not allowed: {content_type}'}
            )
        
        # Generate presigned URL
        presigned_data = generate_presigned_url(
            code=code,
            filename=filename,
            content_type=content_type
        )
        
        # Prepare response
        response_body = {
            'presigned_url': presigned_data['url'],
            'fields': presigned_data['fields'],
            'method': 'POST',
            'expires_in_seconds': PRESIGNED_URL_EXPIRY_SECONDS,
            'max_file_size_mb': MAX_FILE_SIZE_MB,
            'bucket': S3_BUCKET_NAME
        }
        
        logger.info(f"Presigned URL generated successfully for code: {code[:8]}...")
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



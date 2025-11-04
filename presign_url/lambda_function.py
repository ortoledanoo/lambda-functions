"""
Presign URL Lambda Function (Account B).

Generates presigned S3 URLs using STS AssumeRole for better security.
Supports both single-part and multipart uploads.
This Lambda runs in Account B (different account from authorizer/code generator).
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

# Configuration
BUCKET_NAME = os.environ.get('UPLOAD_BUCKET_NAME', '').strip()
if not BUCKET_NAME:
    raise ValueError("UPLOAD_BUCKET_NAME environment variable is required")

MINIMAL_S3_ROLE_ARN = os.environ.get('MINIMAL_S3_ROLE_ARN', '').strip()
if not MINIMAL_S3_ROLE_ARN:
    raise ValueError("MINIMAL_S3_ROLE_ARN environment variable is required")

ALLOWED_CONTENT_TYPES = [t.strip() for t in os.environ.get('ALLOWED_CONTENT_TYPES', '*/*').split(',')]
MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '5000'))  # Default: 5GB for multipart

# Initialize STS client
sts_client = boto3.client('sts')


def get_s3_client_with_assumed_role():
    """
    Get S3 client with credentials from STS AssumeRole.
    
    Returns:
        boto3 S3 client with assumed role credentials
    """
    try:
        logger.info(f"Attempting to assume role: {MINIMAL_S3_ROLE_ARN}")
        response = sts_client.assume_role(
            RoleArn=MINIMAL_S3_ROLE_ARN,
            RoleSessionName=f'PresignedUrlSession-{int(time.time())}',
            DurationSeconds=900  # 15 minutes
        )
        
        if not response.get('Credentials'):
            raise ValueError('No credentials returned from AssumeRole')
        
        credentials = response['Credentials']
        logger.info('Successfully assumed role')
        
        # Get AWS region from environment or use default
        region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'il-central-1'
        
        # Configure S3 client to use regional endpoint
        from botocore.config import Config
        config = Config(
            region_name=region,
            s3={'addressing_style': 'virtual'}
        )
        
        # Create S3 client with assumed role credentials and regional config
        s3_client = boto3.client(
            's3',
            region_name=region,
            config=config,
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken']
        )
        
        return s3_client
        
    except ClientError as e:
        logger.error(f"Failed to assume role: {e}")
        raise


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,x-authorization-words,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS'
        },
        'body': json.dumps(body)
    }


def get_presigned_url(key: str, content_type: str) -> Dict[str, Any]:
    """
    Generate presigned URL for single-part upload.
    
    Args:
        key: S3 object key
        content_type: Content type of the file
        
    Returns:
        Dictionary with presigned URL
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        from botocore.signers import RequestSigner
        from botocore.awsrequest import AWSRequest
        
        # Generate presigned URL
        # Note: ServerSideEncryption not needed - bucket has default encryption
        url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': key,
                'ContentType': content_type
            },
            ExpiresIn=300  # 5 minutes
        )
        
        return {'url': url}
        
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise


def create_multipart_upload(key: str, content_type: str) -> Dict[str, Any]:
    """
    Create multipart upload.
    
    Args:
        key: S3 object key
        content_type: Content type of the file
        
    Returns:
        Dictionary with uploadId
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        response = s3_client.create_multipart_upload(
            Bucket=BUCKET_NAME,
            Key=key,
            ContentType=content_type
            # Note: ServerSideEncryption not needed - bucket has default encryption
        )
        
        return {
            'uploadId': response['UploadId'],
            'key': key
        }
        
    except ClientError as e:
        logger.error(f"Failed to create multipart upload: {e}")
        raise


def get_signed_url_for_part(key: str, upload_id: str, part_number: int) -> Dict[str, Any]:
    """
    Generate presigned URL for a multipart upload part.
    
    Args:
        key: S3 object key
        upload_id: Multipart upload ID
        part_number: Part number (1-indexed)
        
    Returns:
        Dictionary with presigned URL
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        url = s3_client.generate_presigned_url(
            'upload_part',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': key,
                'UploadId': upload_id,
                'PartNumber': part_number
            },
            ExpiresIn=300  # 5 minutes
        )
        
        return {'url': url}
        
    except ClientError as e:
        logger.error(f"Failed to generate signed URL for part: {e}")
        raise


def list_parts(key: str, upload_id: str) -> Dict[str, Any]:
    """
    List parts of a multipart upload.
    
    Args:
        key: S3 object key
        upload_id: Multipart upload ID
        
    Returns:
        Dictionary with parts list
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        response = s3_client.list_parts(
            Bucket=BUCKET_NAME,
            Key=key,
            UploadId=upload_id
        )
        
        parts = []
        if response.get('Parts'):
            for part in response['Parts']:
                parts.append({
                    'PartNumber': part['PartNumber'],
                    'ETag': part['ETag'],
                    'Size': part.get('Size')
                })
        
        return {'parts': parts}
        
    except ClientError as e:
        logger.error(f"Failed to list parts: {e}")
        raise


def complete_multipart_upload(key: str, upload_id: str, parts: list) -> Dict[str, Any]:
    """
    Complete multipart upload.
    
    Args:
        key: S3 object key
        upload_id: Multipart upload ID
        parts: List of parts with ETag and PartNumber
        
    Returns:
        Dictionary with completion result
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        # Convert parts to S3 format
        multipart_parts = [
            {'PartNumber': p['PartNumber'], 'ETag': p['ETag']}
            for p in parts
        ]
        
        response = s3_client.complete_multipart_upload(
            Bucket=BUCKET_NAME,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': multipart_parts}
        )
        
        return {
            'location': response.get('Location'),
            'etag': response.get('ETag'),
            'key': key
        }
        
    except ClientError as e:
        logger.error(f"Failed to complete multipart upload: {e}")
        raise


def abort_multipart_upload(key: str, upload_id: str) -> Dict[str, Any]:
    """
    Abort multipart upload.
    
    Args:
        key: S3 object key
        upload_id: Multipart upload ID
        
    Returns:
        Dictionary with success status
    """
    s3_client = get_s3_client_with_assumed_role()
    
    try:
        s3_client.abort_multipart_upload(
            Bucket=BUCKET_NAME,
            Key=key,
            UploadId=upload_id
        )
        
        return {'success': True}
        
    except ClientError as e:
        logger.error(f"Failed to abort multipart upload: {e}")
        raise


def validate_content_type(content_type: Optional[str]) -> bool:
    """Validate that content type is allowed."""
    # Check for wildcard (allow all)
    if any('*' in allowed or allowed == '*/*' for allowed in ALLOWED_CONTENT_TYPES):
        return True
    
    if not content_type:
        return False
    
    for allowed in ALLOWED_CONTENT_TYPES:
        allowed = allowed.strip()
        if allowed == '*/*' or allowed == '*':
            return True
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
        
        # Extract keyId from authorizer context (required - no fallback)
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        key_id = authorizer_context.get('principalId')
        
        if not key_id:
            return create_response(401, {'error': 'Unauthorized: missing authorizer context'})
        
        # Parse request body
        body = {}
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
        action = body.get('action', 'getPresignedUrl')
        key = body.get('key')
        content_type = body.get('contentType') or body.get('content_type')
        upload_id = body.get('uploadId')
        part_number = body.get('partNumber')
        parts = body.get('parts')
        
        # Validate content type if provided
        if content_type and not validate_content_type(content_type):
            return create_response(400, {'error': f'Content type not allowed: {content_type}'})
        
        # Generate S3 key if not provided
        if not key:
            timestamp = int(time.time())
            filename = body.get('filename', '')
            if filename:
                safe_filename = os.path.basename(filename).replace('..', '').replace('/', '')
                key = f"uploads/{key_id}/{timestamp}-{safe_filename}"
            else:
                key = f"uploads/{key_id}/{timestamp}"
        
        # Handle different actions
        result = None
        if action == 'getPresignedUrl':
            if not content_type:
                return create_response(400, {'error': 'contentType is required for single-part upload'})
            result = get_presigned_url(key, content_type)
            
        elif action == 'createMultipartUpload':
            if not content_type:
                return create_response(400, {'error': 'contentType is required for multipart upload'})
            result = create_multipart_upload(key, content_type)
            
        elif action == 'getSignedUrlForPart':
            if not upload_id or not part_number:
                return create_response(400, {'error': 'uploadId and partNumber are required'})
            result = get_signed_url_for_part(key, upload_id, part_number)
            
        elif action == 'listParts':
            if not upload_id:
                return create_response(400, {'error': 'uploadId is required'})
            result = list_parts(key, upload_id)
            
        elif action == 'completeMultipartUpload':
            if not upload_id or not parts:
                return create_response(400, {'error': 'uploadId and parts are required'})
            result = complete_multipart_upload(key, upload_id, parts)
            
        elif action == 'abortMultipartUpload':
            if not upload_id:
                return create_response(400, {'error': 'uploadId is required'})
            result = abort_multipart_upload(key, upload_id)
            
        else:
            return create_response(400, {'error': f'Unknown action: {action}'})
        
        logger.info(f"Action {action} completed successfully for key: {key}")
        return create_response(200, result)
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(400, {'error': str(e)})
    except ClientError as e:
        logger.error(f"AWS service error: {e}")
        return create_response(500, {'error': f'AWS error: {str(e)}'})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return create_response(500, {'error': 'Internal server error'})

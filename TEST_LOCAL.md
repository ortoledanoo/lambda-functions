# Local Testing Guide

## Prerequisites

1. **AWS CLI configured**: `aws configure`
2. **Python 3.11** with boto3: `pip install boto3`
3. **AWS Resources** (see below)

## AWS Resources to Create

### 1. KMS Key (Account A)
```bash
aws kms create-key \
  --key-spec HMAC_256 \
  --key-usage GENERATE_VERIFY_MAC \
  --description "File whitelist code signing"

  arn:aws:kms:il-central-1:585768175989:key/db534fdb-44e4-426a-b116-8651db1862c2
```
Save the Key ID/ARN.

### 2. DynamoDB Table (Account A)
```bash
aws dynamodb create-table \
  --table-name file-whitelist-codes \
  --attribute-definitions AttributeName=counterId,AttributeType=S \
  --key-schema AttributeName=counterId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

  arn:aws:dynamodb:il-central-1:585768175989:table/file-whitelist-codes
```

### 3. S3 Bucket (Account B)
```bash
aws s3 mb s3://your-test-bucket

your-upload-bucket-name-585768175989
```

### 4. IAM Role for STS AssumeRole (Account B)
```bash
# Create role with trust policy allowing Lambda
aws iam create-role \
  --role-name test-minimal-s3-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

  arn:aws:iam::585768175989:role/test-minimal-s3-role

# Attach S3 policy
aws iam put-role-policy \
  --role-name test-minimal-s3-role \
  --policy-name S3UploadPolicy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:PutObjectAcl"],
      "Resource": "arn:aws:s3:::your-upload-bucket-name-585768175989/*"
    }]
  }'
```
Save the Role ARN.

## Local Testing

### 1. Test Code Generator

```bash
cd code_generator

export KMS_KEY_ID="  arn:aws:kms:il-central-1:585768175989:key/db534fdb-44e4-426a-b116-8651db1862c2"
export DYNAMODB_TABLE_NAME="file-whitelist-codes"
export CODE_EXPIRY_HOURS="24"
export AWS_REGION="us-east-1"

python3 -c "
import json
from lambda_function import lambda_handler
event = {'body': '{}'}
result = lambda_handler(event, None)
print(json.dumps(json.loads(result['body']), indent=2))
"
```

**Expected output:**
```json
{
  "words": "word0001 word0023 ... word0999",
  "expires_in_hours": 24
}
```

**Copy the `words` value for next test.**

### 2. Test Authorizer

```bash
cd ../authorizer

export KMS_KEY_ID="  arn:aws:kms:il-central-1:585768175989:key/db534fdb-44e4-426a-b116-8651db1862c2"
export CODE_EXPIRY_HOURS="24"
export API_GW_ARN="*"
export AWS_REGION="il-central-1"

# Replace WORDS_HERE with words from step 1
python3 -c "
import json
from lambda_function import lambda_handler
event = {
    'body': json.dumps({'words': 'word0001 word0861 word0062 word0204 word0865 word0890 word0711 word0615 word0676 word0318'})
}
result = lambda_handler(event, None)
print(json.dumps(json.loads(result['body']), indent=2))
"
```

**Expected output:**
```json
{
  "valid": true,
  "message": "Code is valid",
  "key_id": 123
}
```

### 3. Test Presign URL

**Note:** Presign URL requires authorizer context (from API Gateway). For local testing, simulate the authorizer context:

```bash
cd ../presign_url

export UPLOAD_BUCKET_NAME="your-upload-bucket-name-585768175989"
export MINIMAL_S3_ROLE_ARN="arn:aws:iam::585768175989:role/test-minimal-s3-role"
export ALLOWED_CONTENT_TYPES="*/*"
export MAX_FILE_SIZE_MB="100"
export AWS_REGION="il-central-1"

# Simulate API Gateway authorizer context (key_id from validated code)
python3 -c "
import json
from lambda_function import lambda_handler
event = {
    'requestContext': {
        'authorizer': {
            'principalId': '123'  # This would come from authorizer after validation
        }
    },
    'body': json.dumps({
        'action': 'getPresignedUrl',
        'contentType': 'application/pdf',
        'filename': 'test.pdf'
    })
}
result = lambda_handler(event, None)
print(json.dumps(json.loads(result['body']), indent=2))
"
```



**Important:** Without `principalId` in authorizer context, the function will return `401 Unauthorized`.

**Expected output:**
```json
{
  "url": "https://your-upload-bucket-name-585768175989.s3.il-central-1.amazonaws.com/uploads/123/..."
}
```

### 4. Test Upload with Presigned URL

After getting the presigned URL from step 3, test the actual upload:

```bash
# Get the URL (save it to a variable)
PRESIGNED_URL=$(python3 -c "
import json
from lambda_function import lambda_handler
event = {
    'requestContext': {'authorizer': {'principalId': '123'}},
    'body': json.dumps({'action': 'getPresignedUrl', 'contentType': 'application/pdf', 'filename': 'test.pdf'})
}
result = lambda_handler(event, None)
print(json.loads(result['body'])['url'])
")

# Upload file using the presigned URL
# IMPORTANT: Don't escape ? and & characters - use quotes around the URL
curl -X PUT "$PRESIGNED_URL" \
  -H "Content-Type: application/pdf" \
  -T test.pdf
```

**Important Notes:**
1. **Don't escape URL parameters**: Use quotes around the URL instead of escaping `\?` and `\&`. Escaping breaks the signature.
2. **No encryption header needed**: The bucket has default encryption, so you don't need to send `x-amz-server-side-encryption` header.
3. **Content-Type must match**: The `Content-Type` header must match what was used to generate the presigned URL.

**Why it failed before:**
- **ServerSideEncryption parameter**: The code was including `ServerSideEncryption: 'AES256'` in presigned URL params, which caused signature mismatch because buckets with default encryption don't require this header.
- **Escaped URL parameters**: Using `\?` and `\&` in curl made it send literal characters instead of query parameters, breaking the signature calculation.

## Test Full Flow

```bash
# 1. Generate code
cd code_generator
export KMS_KEY_ID="..."
export DYNAMODB_TABLE_NAME="file-whitelist-codes"
export CODE_EXPIRY_HOURS="24"
WORDS=$(python3 -c "
from lambda_function import lambda_handler
result = lambda_handler({'body': '{}'}, None)
import json
print(json.loads(result['body'])['words'])
")

# 2. Validate code
cd ../authorizer
export KMS_KEY_ID="..."
export CODE_EXPIRY_HOURS="24"
python3 -c "
import json
from lambda_function import lambda_handler
result = lambda_handler({'body': json.dumps({'words': '$WORDS'})}, None)
print(json.dumps(json.loads(result['body']), indent=2))
"

# 3. Get presigned URL (requires authorizer context)
cd ../presign_url
export UPLOAD_BUCKET_NAME="your-test-bucket"
export MINIMAL_S3_ROLE_ARN="..."
# Note: principalId must come from authorizer validation - simulate it here
python3 -c "
import json
from lambda_function import lambda_handler
result = lambda_handler({
    'requestContext': {'authorizer': {'principalId': '123'}},  # From authorizer
    'body': json.dumps({'action': 'getPresignedUrl', 'contentType': 'application/pdf'})
}, None)
print(json.dumps(json.loads(result['body']), indent=2))
"
```

## Troubleshooting

### Common Issues

- **KMS errors**: Check key permissions and region
- **DynamoDB errors**: Check table exists and region
- **STS errors**: Check role ARN and trust policy
- **S3 errors**: Check bucket exists and role has permissions

### Presigned URL Issues

- **AccessDenied when uploading**: 
  - Make sure you're using the URL in quotes: `curl -X PUT "$URL" ...`
  - Don't escape `?` and `&` characters
  - Ensure Content-Type header matches what was used to generate the URL
  
- **SignatureDoesNotMatch**: 
  - This was caused by including `ServerSideEncryption` in presigned URL params (now fixed)
  - If you see this, ensure the URL is used exactly as generated, without modifications
  
- **Role cannot assume itself**: 
  - If testing locally, make sure your AWS credentials are not from an already-assumed role
  - Unset `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` if set from previous assume-role


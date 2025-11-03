# File Whitelisting Interface - AWS Lambda Backend

A production-grade backend system for secure file upload workflows using AWS Lambda, API Gateway, DynamoDB, S3, and KMS.

## Architecture Overview

This system implements a secure three-step flow for file uploads:

1. **Code Generation**: Users request a signed, time-limited code
2. **Authorization**: Codes are validated before allowing uploads
3. **Presigned URL Generation**: Valid codes receive presigned S3 URLs for file uploads

### Flow Diagram

```
Client → Code Generator Lambda → DynamoDB (store code metadata)
                              ↓
                         Returns code + signature
                              ↓
Client → Authorizer Lambda → DynamoDB (validate code)
                           ↓
                      Verify signature (HMAC/KMS)
                           ↓
                      Return allow/deny
                              ↓
Client → Presign URL Lambda → S3 (generate presigned URL)
                              ↓
                         Returns presigned URL
                              ↓
Client → S3 (upload file using presigned URL)
```

## Components

### 1. Code Generator Lambda (`code_generator/`)

**Purpose**: Generates cryptographically signed codes with expiration.

**Features**:
- HMAC-SHA256 or AWS KMS signing
- Stores metadata in DynamoDB with TTL
- Configurable expiration time
- Prevents duplicate codes

**Input**: None (POST request)

**Output**:
```json
{
  "code": "abc123...",
  "signature": "signature_hex_or_base64",
  "expires_at": 1234567890,
  "expires_in_minutes": 60,
  "signing_method": "HMAC" | "KMS"
}
```

### 2. Authorizer Lambda (`authorizer/`)

**Purpose**: Validates codes and signatures before allowing requests.

**Features**:
- Dual mode: API Gateway authorizer or standalone endpoint
- Signature verification (HMAC or KMS)
- TTL and state validation
- DynamoDB consistency checks

**Input** (Standalone mode):
```json
{
  "code": "abc123...",
  "signature": "signature..."
}
```

**Input** (Authorizer mode):
- `authorizationToken`: `"code:signature"` format in Authorization header

**Output** (Standalone mode):
```json
{
  "valid": true,
  "message": "Code is valid"
}
```

**Output** (Authorizer mode):
- IAM policy document for API Gateway

### 3. Presign URL Lambda (`presign_url/`)

**Purpose**: Generates presigned S3 URLs for authorized file uploads.

**Features**:
- Presigned POST URLs with conditions
- Content type validation
- File size limits
- Organized S3 key structure

**Input**:
```json
{
  "filename": "example.pdf",  // Optional
  "content_type": "application/pdf"  // Optional
}
```

**Output**:
```json
{
  "presigned_url": "https://s3.amazonaws.com/...",
  "fields": {
    "key": "...",
    "AWSAccessKeyId": "...",
    "policy": "...",
    "signature": "..."
  },
  "method": "POST",
  "expires_in_seconds": 3600,
  "max_file_size_mb": 100,
  "bucket": "your-bucket-name"
}
```

## Prerequisites

- AWS Account with appropriate permissions
- AWS CLI configured
- Python 3.11
- SAM CLI (for deployment)
- Docker (for local testing with SAM)

## AWS Resources Required

### DynamoDB Table
- **Table Name**: `file-whitelist-codes` (configurable)
- **Partition Key**: `code` (String)
- **TTL Attribute**: `ttl` (Number)
- **Billing Mode**: Pay-per-request (recommended)

### S3 Bucket
- **Name**: Your choice (must be globally unique)
- **Encryption**: Server-side encryption enabled
- **Public Access**: Blocked
- **Lifecycle**: Optional (for automatic cleanup)

### KMS Key (Optional)
- **Usage**: SIGN_VERIFY
- **Key Spec**: RSA_2048
- **Alias**: `alias/file-whitelist-signing-{environment}`

### IAM Roles
- Lambda execution role with permissions for:
  - DynamoDB (GetItem, PutItem, UpdateItem, Query)
  - S3 (PutObject, GetObject)
  - KMS (Sign, Verify, DescribeKey)
  - CloudWatch Logs

## Environment Variables

### Code Generator Lambda
- `DYNAMODB_TABLE_NAME` (required): DynamoDB table name
- `USE_KMS` (optional): `true` or `false` (default: `false`)
- `KMS_KEY_ID` (required if USE_KMS=true): KMS key ID or ARN
- `HMAC_SECRET` (required if USE_KMS=false): Secret key for HMAC signing
- `CODE_EXPIRY_MINUTES` (optional): Code expiration in minutes (default: 60)

### Authorizer Lambda
- `DYNAMODB_TABLE_NAME` (required): DynamoDB table name
- `USE_KMS` (optional): `true` or `false` (default: `false`)
- `KMS_KEY_ID` (required if USE_KMS=true): KMS key ID or ARN
- `HMAC_SECRET` (required if USE_KMS=false): Secret key for HMAC signing

### Presign URL Lambda
- `S3_BUCKET_NAME` (required): S3 bucket name for uploads
- `PRESIGNED_URL_EXPIRY_SECONDS` (optional): URL expiration in seconds (default: 3600)
- `ALLOWED_CONTENT_TYPES` (optional): Comma-separated list (default: `*/*`)
- `MAX_FILE_SIZE_MB` (optional): Maximum file size in MB (default: 100)

## Deployment

### Using AWS SAM

1. **Install SAM CLI**:
   ```bash
   pip install aws-sam-cli
   ```

2. **Configure parameters**:
   Edit `template.yaml` or use `samconfig.toml`:
   ```bash
   sam deploy --guided
   ```

3. **Build and deploy**:
   ```bash
   sam build
   sam deploy
   ```

### Manual Deployment

1. **Create AWS resources**:
   - Create DynamoDB table (see schema above)
   - Create S3 bucket
   - Create KMS key (if using KMS)
   - Create IAM role with required permissions

2. **Package Lambda functions**:
   ```bash
   # For each Lambda function (include shared module)
   cd code_generator
   cp -r ../shared .
   pip install -r ../requirements.txt -t .
   zip -r ../code_generator.zip .
   cd ..
   
   # Repeat for authorizer and presign_url
   cd authorizer
   cp -r ../shared .
   pip install -r ../requirements.txt -t .
   zip -r ../authorizer.zip .
   cd ..
   
   cd presign_url
   cp -r ../shared .
   pip install -r ../requirements.txt -t .
   zip -r ../presign_url.zip .
   ```

3. **Deploy functions**:
   - Upload ZIP files to AWS Lambda
   - Configure environment variables
   - Set up API Gateway endpoints
   - Configure Lambda authorizer (if using)

## Local Testing

### Prerequisites
- Docker (for SAM local)
- AWS credentials configured

### Test Code Generator

```bash
# Using SAM local
sam local invoke CodeGeneratorFunction \
  --event events/generate-code-event.json

# Or using Python directly
cd code_generator
python -c "
import json
from lambda_function import lambda_handler
event = {'body': '{}'}
result = lambda_handler(event, None)
print(json.dumps(result, indent=2))
"
```

### Test Authorizer

```bash
# Standalone validation
sam local invoke AuthorizerFunction \
  --event events/validate-code-event.json

# Authorizer mode (for API Gateway)
sam local invoke AuthorizerFunction \
  --event events/authorizer-event.json
```

### Test Presign URL

```bash
sam local invoke PresignURLFunction \
  --event events/presign-url-event.json
```

### Example Event Files

Create `events/generate-code-event.json`:
```json
{
  "body": "{}"
}
```

Create `events/validate-code-event.json`:
```json
{
  "body": "{\"code\": \"your_code_here\", \"signature\": \"your_signature_here\"}"
}
```

Create `events/presign-url-event.json`:
```json
{
  "requestContext": {
    "authorizer": {
      "principalId": "your_code_here"
    }
  },
  "body": "{\"filename\": \"test.pdf\", \"content_type\": \"application/pdf\"}"
}
```

## Testing via API Gateway

### 1. Generate Code

```bash
curl -X POST https://your-api-id.execute-api.region.amazonaws.com/Prod/generate-code \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Response**:
```json
{
  "code": "abc123def456...",
  "signature": "signature...",
  "expires_at": 1234567890,
  "expires_in_minutes": 60,
  "signing_method": "HMAC"
}
```

### 2. Validate Code (Standalone)

```bash
curl -X POST https://your-api-id.execute-api.region.amazonaws.com/Prod/validate-code \
  -H "Content-Type: application/json" \
  -d '{
    "code": "abc123def456...",
    "signature": "signature..."
  }'
```

### 3. Get Presigned URL

**With API Gateway Authorizer**:
```bash
curl -X POST https://your-api-id.execute-api.region.amazonaws.com/Prod/presign-url \
  -H "Authorization: abc123def456...:signature..." \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "document.pdf",
    "content_type": "application/pdf"
  }'
```

**Without Authorizer** (if endpoint allows):
```bash
curl -X POST https://your-api-id.execute-api.region.amazonaws.com/Prod/presign-url \
  -H "Content-Type: application/json" \
  -d '{
    "code": "abc123def456...",
    "filename": "document.pdf",
    "content_type": "application/pdf"
  }'
```

**Response**:
```json
{
  "presigned_url": "https://s3.amazonaws.com/bucket/...",
  "fields": {
    "key": "uploads/code/timestamp-filename",
    "Content-Type": "application/pdf",
    "AWSAccessKeyId": "...",
    "policy": "...",
    "signature": "..."
  },
  "method": "POST",
  "expires_in_seconds": 3600,
  "max_file_size_mb": 100,
  "bucket": "your-bucket-name"
}
```

### 4. Upload File

```bash
curl -X POST "https://s3.amazonaws.com/your-bucket/..." \
  -F "key=uploads/code/timestamp-filename" \
  -F "Content-Type=application/pdf" \
  -F "AWSAccessKeyId=..." \
  -F "policy=..." \
  -F "signature=..." \
  -F "file=@document.pdf"
```

## Security Considerations

1. **HMAC Secret**: Store in AWS Secrets Manager or Parameter Store (not hardcoded)
2. **CORS**: Configure `Access-Control-Allow-Origin` appropriately for production
3. **KMS**: Use KMS for production environments (better key management)
4. **IAM Roles**: Follow principle of least privilege
5. **S3 Bucket**: Block public access, use bucket policies
6. **DynamoDB**: Enable encryption at rest
7. **Code Expiration**: Keep expiration times short (recommended: 60 minutes or less)
8. **Rate Limiting**: Implement rate limiting at API Gateway level

## Monitoring and Logging

- **CloudWatch Logs**: All Lambda functions log to CloudWatch
- **Metrics**: Monitor:
  - Code generation rate
  - Authorization success/failure rates
  - Presigned URL generation
  - DynamoDB read/write capacity
  - S3 upload success rates

## Troubleshooting

### Code Generation Fails
- Check DynamoDB table exists and Lambda has permissions
- Verify HMAC_SECRET or KMS_KEY_ID is set correctly
- Check CloudWatch Logs for detailed error messages

### Authorization Fails
- Verify code hasn't expired
- Check signature matches (HMAC/KMS)
- Ensure DynamoDB item exists and state is "active"
- Verify environment variables match code generator settings

### Presigned URL Fails
- Check S3 bucket exists and Lambda has permissions
- Verify bucket name is correct
- Check content type is allowed (if restrictions set)
- Ensure file size is within limits

## Directory Structure

```
lambda-functions/
├── code_generator/
│   └── lambda_function.py
├── authorizer/
│   └── lambda_function.py
├── presign_url/
│   └── lambda_function.py
├── shared/
│   └── utils.py
├── template.yaml
├── requirements.txt
└── README.md
```

## License

This code is provided as-is for production use. Ensure proper security reviews before deploying to production environments.


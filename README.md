# File Whitelisting Interface - AWS Lambda Backend

A production-grade backend system for secure file upload workflows using AWS Lambda, API Gateway, DynamoDB, S3, and KMS.

## Architecture Overview

This system implements a secure three-step flow for file uploads across **two AWS accounts**:

- **Account A**: Code Generator Lambda (generates signed codes)
- **Account B**: Authorizer Lambda + Presign URL Lambda (validates codes and generates S3 URLs)

### Two-Account Architecture

```
Account A (Code Generation):
├── Code Generator Lambda
└── DynamoDB Table (shared with Account B)

Account B (Authorization & Upload):
├── Authorizer Lambda
├── Presign URL Lambda
└── S3 Bucket (for file uploads)
```

### Flow Diagram

```
1. Client → Code Generator Lambda (Account A) → DynamoDB (store code metadata)
                                        ↓
                                   Returns code + signature
                                        ↓
2. Client → Authorizer Lambda (Account B) → DynamoDB (validate code)
                                          ↓
                                     Verify signature (HMAC/KMS)
                                          ↓
                                     Return allow/deny
                                        ↓
3. Client → Presign URL Lambda (Account B) → S3 (generate presigned URL)
                                          ↓
                                     Returns presigned URL
                                        ↓
4. Client → S3 (upload file using presigned URL)
```

## Components

### 1. Code Generator Lambda (`code_generator/`) - Account A

**Purpose**: Generates cryptographically signed codes with expiration.

**Features**:
- HMAC-SHA256 or AWS KMS signing
- Stores metadata in DynamoDB with TTL
- Configurable expiration time
- Standalone function (no shared dependencies)

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

### 2. Authorizer Lambda (`authorizer/`) - Account B

**Purpose**: Validates codes and signatures before allowing requests.

**Features**:
- Dual mode: API Gateway authorizer or standalone endpoint
- Signature verification (HMAC or KMS)
- TTL and state validation
- DynamoDB consistency checks
- Standalone function (no shared dependencies)

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

### 3. Presign URL Lambda (`presign_url/`) - Account B

**Purpose**: Generates presigned S3 URLs for authorized file uploads.

**Features**:
- Presigned POST URLs with conditions
- Content type validation
- File size limits
- Organized S3 key structure
- Standalone function (no shared dependencies)

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

- Two AWS Accounts (Account A and Account B)
- AWS CLI configured with credentials for both accounts
- Python 3.11
- Access to create Lambda functions, DynamoDB tables, S3 buckets, and IAM roles

## AWS Resources Required

### Shared Resources (must be accessible from both accounts)

#### DynamoDB Table
- **Table Name**: `file-whitelist-codes` (configurable)
- **Partition Key**: `code` (String)
- **TTL Attribute**: `ttl` (Number)
- **Billing Mode**: Pay-per-request (recommended)
- **Cross-Account Access**: Account B needs read access to this table

### Account A Resources

- **Code Generator Lambda Function**
- **DynamoDB Table** (or access to shared table)
- **KMS Key** (optional, if using KMS signing)
- **IAM Role** for Lambda with DynamoDB write permissions

### Account B Resources

- **Authorizer Lambda Function**
- **Presign URL Lambda Function**
- **S3 Bucket** for file uploads
- **API Gateway** (optional, for HTTP endpoints)
- **IAM Roles** for Lambda functions

## Environment Variables

### Code Generator Lambda (Account A)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DYNAMODB_TABLE_NAME` | Yes | - | DynamoDB table name |
| `USE_KMS` | No | `false` | Use KMS for signing (`true`/`false`) |
| `KMS_KEY_ID` | If USE_KMS=true | - | KMS key ID or ARN |
| `HMAC_SECRET` | If USE_KMS=false | - | Secret key for HMAC signing |
| `CODE_EXPIRY_MINUTES` | No | `60` | Code expiration in minutes |

### Authorizer Lambda (Account B)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DYNAMODB_TABLE_NAME` | Yes | - | DynamoDB table name (must match Account A) |
| `USE_KMS` | No | `false` | Use KMS for signing (`true`/`false`) |
| `KMS_KEY_ID` | If USE_KMS=true | - | KMS key ID or ARN (must match Account A) |
| `HMAC_SECRET` | If USE_KMS=false | - | Secret key for HMAC signing (must match Account A) |

### Presign URL Lambda (Account B)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `S3_BUCKET_NAME` | Yes | - | S3 bucket name for uploads |
| `PRESIGNED_URL_EXPIRY_SECONDS` | No | `3600` | URL expiration in seconds |
| `ALLOWED_CONTENT_TYPES` | No | `*/*` | Comma-separated list of allowed content types |
| `MAX_FILE_SIZE_MB` | No | `100` | Maximum file size in MB |

## Deployment

### Step 1: Create DynamoDB Table (Shared)

Create the table in Account A (or a shared account):

```bash
aws dynamodb create-table \
  --table-name file-whitelist-codes \
  --attribute-definitions AttributeName=code,AttributeType=S \
  --key-schema AttributeName=code,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --time-to-live-specification Enabled=true,AttributeName=ttl \
  --profile account-a
```

### Step 2: Set Up Cross-Account Access (Account B → DynamoDB)

Create a policy in Account A to allow Account B to read from DynamoDB:

```bash
# In Account A, create a policy document (dynamodb-policy.json)
cat > dynamodb-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "AWS": "arn:aws:iam::ACCOUNT_B_ID:root"
    },
    "Action": [
      "dynamodb:GetItem",
      "dynamodb:Query"
    ],
    "Resource": "arn:aws:dynamodb:REGION:ACCOUNT_A_ID:table/file-whitelist-codes"
  }]
}
EOF

# Attach to DynamoDB table (requires AWS CLI or console)
```

Alternatively, use resource-based policies or IAM role assumption.

### Step 3: Deploy Code Generator Lambda (Account A)

```bash
cd code_generator

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../code_generator.zip .

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-code-generator \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_A_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../code_generator.zip \
  --environment Variables="{
    DYNAMODB_TABLE_NAME=file-whitelist-codes,
    USE_KMS=false,
    HMAC_SECRET=your-secret-here,
    CODE_EXPIRY_MINUTES=60
  }" \
  --profile account-a

# Create API Gateway endpoint (optional)
aws apigatewayv2 create-api \
  --name file-whitelist-api \
  --protocol-type HTTP \
  --profile account-a
```

### Step 4: Deploy Authorizer Lambda (Account B)

```bash
cd authorizer

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../authorizer.zip .

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-authorizer \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_B_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../authorizer.zip \
  --environment Variables="{
    DYNAMODB_TABLE_NAME=file-whitelist-codes,
    USE_KMS=false,
    HMAC_SECRET=your-secret-here
  }" \
  --profile account-b
```

### Step 5: Deploy Presign URL Lambda (Account B)

```bash
cd presign_url

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../presign_url.zip .

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-presign-url \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_B_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../presign_url.zip \
  --environment Variables="{
    S3_BUCKET_NAME=your-upload-bucket,
    PRESIGNED_URL_EXPIRY_SECONDS=3600,
    ALLOWED_CONTENT_TYPES=*/*,
    MAX_FILE_SIZE_MB=100
  }" \
  --profile account-b
```

### Step 6: Create S3 Bucket (Account B)

```bash
aws s3 mb s3://your-upload-bucket --profile account-b

aws s3api put-bucket-encryption \
  --bucket your-upload-bucket \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }' \
  --profile account-b
```

### Step 7: Configure API Gateway (Account B)

If using API Gateway, configure the authorizer:

1. Create API Gateway REST API or HTTP API
2. Set up Lambda authorizer pointing to `file-whitelist-authorizer`
3. Configure `/presign-url` endpoint with authorizer
4. Deploy API

## IAM Roles Required

### Account A - Code Generator Lambda Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/file-whitelist-codes"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:Sign",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:*:*:key/*"
    }
  ]
}
```

### Account B - Authorizer Lambda Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:Query"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/file-whitelist-codes"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:Verify",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:*:*:key/*"
    }
  ]
}
```

### Account B - Presign URL Lambda Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::your-upload-bucket/*"
    }
  ]
}
```

## Testing

### Test Code Generator (Account A)

```bash
# Invoke directly
aws lambda invoke \
  --function-name file-whitelist-code-generator \
  --payload '{"body": "{}"}' \
  --profile account-a \
  response.json

cat response.json
```

### Test Authorizer (Account B)

```bash
# Standalone validation
aws lambda invoke \
  --function-name file-whitelist-authorizer \
  --payload '{"body": "{\"code\": \"your_code\", \"signature\": \"your_signature\"}"}' \
  --profile account-b \
  response.json
```

### Test Presign URL (Account B)

```bash
aws lambda invoke \
  --function-name file-whitelist-presign-url \
  --payload '{"requestContext": {"authorizer": {"principalId": "your_code"}}, "body": "{\"filename\": \"test.pdf\"}"}' \
  --profile account-b \
  response.json
```

## Testing via API Gateway

### 1. Generate Code (Account A)

```bash
curl -X POST https://api-account-a.execute-api.region.amazonaws.com/generate-code \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 2. Get Presigned URL (Account B)

```bash
curl -X POST https://api-account-b.execute-api.region.amazonaws.com/presign-url \
  -H "Authorization: code:signature" \
  -H "Content-Type: application/json" \
  -d '{"filename": "document.pdf", "content_type": "application/pdf"}'
```

## Security Considerations

1. **HMAC Secret**: Store in AWS Secrets Manager (same secret in both accounts)
2. **KMS Keys**: Use KMS for production (same key in both accounts or cross-account access)
3. **Cross-Account Access**: Use IAM roles with least privilege
4. **DynamoDB**: Enable encryption at rest, restrict access via IAM
5. **S3 Bucket**: Block public access, use bucket policies
6. **Code Expiration**: Keep expiration times short (recommended: 60 minutes)
7. **Rate Limiting**: Implement at API Gateway level
8. **CORS**: Configure appropriately for your frontend

## Important Notes

- **Shared Secrets**: HMAC_SECRET or KMS_KEY_ID must be identical in Account A and Account B
- **DynamoDB Access**: Account B needs read access to the table in Account A
- **No Shared Code**: Each Lambda is standalone - no shared dependencies
- **Cross-Account Setup**: Requires proper IAM policies for cross-account DynamoDB access

## Directory Structure

```
lambda-functions/
├── code_generator/
│   └── lambda_function.py          # Standalone (Account A)
├── authorizer/
│   └── lambda_function.py          # Standalone (Account B)
├── presign_url/
│   └── lambda_function.py          # Standalone (Account B)
├── events/                          # Example event files
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

## Troubleshooting

### Code Generation Fails
- Check DynamoDB table exists in Account A
- Verify Lambda role has DynamoDB write permissions
- Check HMAC_SECRET or KMS_KEY_ID is set correctly

### Authorization Fails
- Verify code hasn't expired
- Check signature matches (same HMAC_SECRET or KMS key in both accounts)
- Ensure DynamoDB item exists and state is "active"
- Verify Account B has read access to DynamoDB table in Account A

### Presigned URL Fails
- Check S3 bucket exists in Account B
- Verify Lambda role has S3 PutObject permission
- Check bucket name is correct in environment variables

## License

This code is provided as-is for production use. Ensure proper security reviews before deploying to production environments.

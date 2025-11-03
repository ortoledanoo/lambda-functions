# File Whitelisting Interface - AWS Lambda Backend

A production-grade backend system for secure file upload workflows using AWS Lambda, API Gateway, DynamoDB, S3, and KMS with **pure cryptographic validation**.

## Architecture Overview

This system implements a secure three-step flow for file uploads across **two AWS accounts**:

- **Account A**: Code Generator Lambda (generates word-based signed codes)
- **Account B**: Authorizer Lambda + Presign URL Lambda (validates codes cryptographically and generates S3 URLs)

### Two-Account Architecture

```
Account A (Code Generation):
├── Code Generator Lambda
└── DynamoDB Table (only for daily counter)

Account B (Authorization & Upload):
├── Authorizer Lambda (pure cryptographic validation - no DynamoDB)
├── Presign URL Lambda (STS AssumeRole + multipart support)
└── S3 Bucket (for file uploads)
```

### Flow Diagram

```
1. Client → Code Generator Lambda (Account A) 
   → DynamoDB (increment daily counter)
   → KMS (generate MAC: counter|date|hours)
   → Encode as 10 words
   → Returns: "word0001 word0023 ... word0999"
                                        ↓
2. Client → Authorizer Lambda (Account B)
   → Decode words to bits
   → Regenerate MAC with KMS (try hours within TTL window)
   → Validate signature + check TTL
   → Return allow/deny (stateless - no DynamoDB needed!)
                                        ↓
3. Client → Presign URL Lambda (Account B)
   → STS AssumeRole (temporary S3 credentials)
   → Generate presigned URL (single-part or multipart)
   → Returns presigned URL
                                        ↓
4. Client → S3 (upload file using presigned URL)
```

## Key Features

- **Word-Based Codes**: User-friendly codes (10 words) instead of hex/base64
- **Pure Cryptographic Validation**: No DynamoDB lookup needed for validation (stateless)
- **STS AssumeRole**: More secure S3 access using temporary credentials
- **Multipart Upload Support**: Handles large files efficiently
- **Daily Counter**: Prevents code reuse with daily counter
- **TTL Validation**: Configurable expiration (hours-based)
- **Secure API Gateway Integration**: Presigned URL requires authorizer context (no bypass)

## How the Cryptographic System Works

### Understanding HMAC and Code Generation

Think of HMAC (Hash-based Message Authentication Code) like a special signature that only the correct key can create. Here's how it works in simple terms:

#### 1. Code Generation (Account A)

When a user requests a code, the system:

1. **Gets a daily counter**: Increments a number in DynamoDB (one per day). This ensures each code is unique.
2. **Builds a message**: Combines three parts:
   - Counter (10 bits): The daily counter number (0-1023)
   - Date (YYYY-MM-DD): Today's date
   - Hours (number): Current hours since epoch (for TTL validation)
   
   Example: `0000001010|2024-01-15|1234567`

3. **Creates a MAC signature**: Uses AWS KMS to generate an HMAC signature of this message. KMS takes the message and a secret key, and produces a unique "signature" that can't be forged.
   
   - The MAC is like a fingerprint: same message + same key = same MAC
   - Different message or different key = different MAC
   - You can't create a valid MAC without the secret key

4. **Encodes as words**: Takes the first 100 bits (10-bit counter + 90-bit MAC) and converts them into 10 human-readable words.
   
   - Each word represents 10 bits (0-1023)
   - Dictionary: `word0000`, `word0001`, ... `word1023`
   - Example output: `word0001 word0023 word0456 word0789 word0123 word0456 word0789 word0123 word0456 word0789`

#### 2. Code Validation (Account B)

When a user provides a code, the system:

1. **Decodes the words**: Converts the 10 words back into 100 bits
   - Extracts counter (first 10 bits)
   - Extracts MAC signature (last 90 bits)

2. **Tries to recreate the signature**: 
   - Builds the same message format: `counter|date|hours`
   - Tries different hours within the TTL window (to handle clock differences)
   - Uses KMS to generate a MAC for each attempt

3. **Compares signatures**: 
   - If the generated MAC matches the MAC from the code → **valid**
   - If they don't match → **invalid** (code is fake or expired)

4. **Checks expiration**: 
   - If a valid MAC is found, checks if the hours are within the allowed TTL window
   - If code is older than TTL → **expired**

#### Why This Is Secure

- **Cryptographic proof**: You can't fake a valid MAC without the KMS key. Even if someone sees a valid code, they can't create a new one.
- **Time-bound**: Codes expire after X hours, preventing long-term abuse.
- **Stateless**: No database lookup needed. The code itself proves its validity.
- **Unique**: Daily counter ensures no code is reused on the same day.

#### Simple Analogy

Think of it like a concert ticket:
- **Counter**: Ticket number (unique per day)
- **Date**: Show date
- **Hours**: Time of purchase (for expiration)
- **MAC**: A holographic sticker that can only be created by the ticket printer (KMS key)
- **Validation**: The bouncer (authorizer) checks if the hologram matches what the printer would create for that ticket number, date, and time

Without the real printer (KMS key), you can't create a valid ticket (code).

## Components

### 1. Code Generator Lambda (`code_generator/`) - Account A

**Purpose**: Generates KMS-signed codes using word-based encoding.

**How it works**:
1. Increments daily counter in DynamoDB (counterId: `code-count-YYYY-MM-DD`)
2. Builds message: `counter|date|hours_since_epoch`
3. Generates MAC using KMS HMAC_SHA_256
4. Encodes 100 bits (10-bit counter + 90-bit MAC) as 10 words
5. Returns space-separated words

**Features**:
- KMS HMAC_SHA_256 signing
- Daily counter prevents reuse
- Word-based encoding (user-friendly)
- Configurable TTL (hours)

**Input**: None (POST request)

**Output**:
```json
{
  "words": "word0001 word0023 word0456 word0789 word0123 word0456 word0789 word0123 word0456 word0789",
  "expires_in_hours": 24
}
```

**Environment Variables**:
- `KMS_KEY_ID` (required): KMS key ID or ARN
- `DYNAMODB_TABLE_NAME` (optional): Table for counter (default: `file-whitelist-codes`)
- `CODE_EXPIRY_HOURS` (optional): TTL in hours (default: 24)

### 2. Authorizer Lambda (`authorizer/`) - Account B

**Purpose**: Validates codes using pure cryptographic validation (no DynamoDB).

**How it works**:
1. Decodes 10 words back to 100 bits
2. Extracts counter (10 bits) and MAC (90 bits)
3. Tries regenerating MAC with hours within TTL window
4. Compares MACs - if match found within TTL, code is valid
5. Returns IAM policy (for API Gateway) or validation result

**Features**:
- **Stateless validation** - no DynamoDB needed
- Pure cryptographic verification
- TTL checking (hours-based)
- Clock skew tolerance
- Dual mode: API Gateway authorizer or standalone endpoint

**Input** (Standalone mode):
```json
{
  "words": "word0001 word0023 ... word0999"
}
```

Or via header:
```
X-Authorization-Words: word0001 word0023 ... word0999
```

**Input** (Authorizer mode):
- `authorizationToken`: Words string in Authorization header

**Output** (Standalone mode):
```json
{
  "valid": true,
  "message": "Code is valid",
  "key_id": 123
}
```

**Output** (Authorizer mode):
- IAM policy document for API Gateway

**Environment Variables**:
- `KMS_KEY_ID` (required): KMS key ID or ARN (must match Account A)
- `CODE_EXPIRY_HOURS` (optional): TTL in hours (default: 24, must match Account A)
- `API_GW_ARN` (optional): API Gateway ARN for policy (default: `*`)

### 3. Presign URL Lambda (`presign_url/`) - Account B

**Purpose**: Generates presigned S3 URLs using STS AssumeRole for better security.

**Features**:
- **STS AssumeRole**: Temporary credentials (more secure than direct presigned URLs)
- **Multipart upload support**: For large files
- **Single-part upload**: For smaller files
- Content type validation
- File size limits
- **Requires Authorizer Context**: Can only be called through API Gateway with valid authorizer (no bypass)

**Security**: This function **requires** `principalId` from API Gateway authorizer context. It cannot be called directly without going through the authorizer validation first.

**Input**:
```json
{
  "action": "getPresignedUrl" | "createMultipartUpload" | "getSignedUrlForPart" | "listParts" | "completeMultipartUpload" | "abortMultipartUpload",
  "key": "uploads/123/filename.pdf",  // Optional, auto-generated if not provided
  "contentType": "application/pdf",    // Required for uploads
  "filename": "document.pdf",          // Optional, used for key generation
  "uploadId": "...",                   // Required for multipart operations
  "partNumber": 1,                     // Required for getSignedUrlForPart
  "parts": [...]                       // Required for completeMultipartUpload
}
```

**Output** (single-part):
```json
{
  "url": "https://s3.amazonaws.com/bucket/..."
}
```

**Output** (multipart create):
```json
{
  "uploadId": "abc123...",
  "key": "uploads/123/filename.pdf"
}
```

**Output** (multipart part):
```json
{
  "url": "https://s3.amazonaws.com/bucket/...?uploadId=..."
}
```

**Environment Variables**:
- `UPLOAD_BUCKET_NAME` (required): S3 bucket name
- `MINIMAL_S3_ROLE_ARN` (required): IAM role ARN for STS AssumeRole
- `ALLOWED_CONTENT_TYPES` (optional): Comma-separated list (default: `*/*`)
- `MAX_FILE_SIZE_MB` (optional): Max file size in MB (default: 5000)

## Prerequisites

- Two AWS Accounts (Account A and Account B)
- AWS CLI configured with credentials for both accounts
- Python 3.11
- KMS key with `GenerateMac` and `VerifyMac` permissions (shared or cross-account access)
- S3 bucket in Account B
- IAM role for STS AssumeRole (in Account B)

## AWS Resources Required

### Account A Resources

- **Code Generator Lambda Function**
- **DynamoDB Table**: For daily counter only
  - Table name: `file-whitelist-codes` (configurable)
  - Partition key: `counterId` (String)
  - Billing mode: Pay-per-request
- **KMS Key**: For code signing
  - Key usage: `GenerateMac`
  - Algorithm: `HMAC_SHA_256`
  - Must be accessible from Account B (or shared)

### Account B Resources

- **Authorizer Lambda Function**
- **Presign URL Lambda Function**
- **S3 Bucket**: For file uploads
- **IAM Role**: For STS AssumeRole (minimal S3 permissions)
- **KMS Key Access**: Read access to same KMS key as Account A
- **API Gateway** (optional): For HTTP endpoints

## Environment Variables Summary

### Code Generator Lambda (Account A)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KMS_KEY_ID` | Yes | - | KMS key ID or ARN |
| `DYNAMODB_TABLE_NAME` | No | `file-whitelist-codes` | DynamoDB table for counter |
| `CODE_EXPIRY_HOURS` | No | `24` | Code expiration in hours |

### Authorizer Lambda (Account B)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KMS_KEY_ID` | Yes | - | KMS key ID or ARN (must match Account A) |
| `CODE_EXPIRY_HOURS` | No | `24` | TTL in hours (must match Account A) |
| `API_GW_ARN` | No | `*` | API Gateway ARN for policy |

### Presign URL Lambda (Account B)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `UPLOAD_BUCKET_NAME` | Yes | - | S3 bucket name |
| `MINIMAL_S3_ROLE_ARN` | Yes | - | IAM role ARN for STS AssumeRole |
| `ALLOWED_CONTENT_TYPES` | No | `*/*` | Comma-separated allowed types |
| `MAX_FILE_SIZE_MB` | No | `5000` | Maximum file size in MB |

## Deployment

### Step 1: Create DynamoDB Table (Account A)

Only needed for daily counter:

```bash
aws dynamodb create-table \
  --table-name file-whitelist-codes \
  --attribute-definitions AttributeName=counterId,AttributeType=S \
  --key-schema AttributeName=counterId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --profile account-a
```

### Step 2: Create KMS Key (Shared or Cross-Account)

Create KMS key with HMAC_SHA_256 support:

```bash
# In Account A
aws kms create-key \
  --description "File whitelist code signing" \
  --key-spec HMAC_256 \
  --key-usage GENERATE_VERIFY_MAC \
  --profile account-a

# Grant Account B access (or use shared key)
aws kms create-grant \
  --key-id <KEY_ID> \
  --grantee-principal arn:aws:iam::ACCOUNT_B_ID:root \
  --operations GenerateMac VerifyMac \
  --profile account-a
```

### Step 3: Deploy Code Generator Lambda (Account A)

```bash
cd code_generator

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../code_generator.zip . -x "*.pyc" "__pycache__/*"

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-code-generator \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_A_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../code_generator.zip \
  --environment Variables="{
    KMS_KEY_ID=arn:aws:kms:region:ACCOUNT_A_ID:key/KEY_ID,
    DYNAMODB_TABLE_NAME=file-whitelist-codes,
    CODE_EXPIRY_HOURS=24
  }" \
  --profile account-a
```

### Step 4: Configure API Gateway Authorizer (Account B)

Before deploying presign URL, set up API Gateway with Lambda Authorizer:

1. Create API Gateway REST API or HTTP API
2. Configure Lambda Authorizer:
   - Type: Token
   - Authorizer Lambda: `file-whitelist-authorizer`
   - Token Source: `Authorization` header
   - Identity Source: Leave default

3. Configure `/presign-url` endpoint:
   - Method: POST
   - Authorization: Use the Lambda Authorizer
   - Integration: Lambda function `file-whitelist-presign-url`

### Step 5: Deploy Authorizer Lambda (Account B)

```bash
cd authorizer

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../authorizer.zip . -x "*.pyc" "__pycache__/*"

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-authorizer \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_B_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../authorizer.zip \
  --environment Variables="{
    KMS_KEY_ID=arn:aws:kms:region:ACCOUNT_A_ID:key/KEY_ID,
    CODE_EXPIRY_HOURS=24,
    API_GW_ARN=arn:aws:execute-api:region:ACCOUNT_B_ID:api-id/*/*
  }" \
  --profile account-b
```

### Step 5: Create IAM Role for STS AssumeRole (Account B)

Create a minimal S3 role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl",
        "s3:CreateMultipartUpload",
        "s3:UploadPart",
        "s3:CompleteMultipartUpload",
        "s3:AbortMultipartUpload",
        "s3:ListParts"
      ],
      "Resource": "arn:aws:s3:::your-upload-bucket/*"
    }
  ]
}
```

Allow Presign URL Lambda to assume this role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "lambda.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

### Step 6: Deploy Presign URL Lambda (Account B)

```bash
cd presign_url

# Install dependencies
pip install boto3 -t .

# Create deployment package
zip -r ../presign_url.zip . -x "*.pyc" "__pycache__/*"

# Create Lambda function
aws lambda create-function \
  --function-name file-whitelist-presign-url \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT_B_ID:role/lambda-execution-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://../presign_url.zip \
  --environment Variables="{
    UPLOAD_BUCKET_NAME=your-upload-bucket,
    MINIMAL_S3_ROLE_ARN=arn:aws:iam::ACCOUNT_B_ID:role/minimal-s3-role,
    ALLOWED_CONTENT_TYPES=*/*,
    MAX_FILE_SIZE_MB=5000
  }" \
  --profile account-b
```

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
        "dynamodb:UpdateItem",
        "dynamodb:GetItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/file-whitelist-codes"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:GenerateMac"
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
        "kms:GenerateMac"
      ],
      "Resource": "arn:aws:kms:*:*:key/*"
    }
  ]
}
```

Note: **No DynamoDB permissions needed** - pure cryptographic validation!

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
        "sts:AssumeRole"
      ],
      "Resource": "arn:aws:iam::ACCOUNT_B_ID:role/minimal-s3-role"
    }
  ]
}
```

## Testing

### Test Code Generator (Account A)

```bash
aws lambda invoke \
  --function-name file-whitelist-code-generator \
  --payload '{"body": "{}"}' \
  --profile account-a \
  response.json

cat response.json
# Output: {"words": "word0001 word0023 ... word0999", "expires_in_hours": 24}
```

### Test Authorizer (Account B)

```bash
# Standalone validation
aws lambda invoke \
  --function-name file-whitelist-authorizer \
  --payload '{"body": "{\"words\": \"word0001 word0023 word0456 word0789 word0123 word0456 word0789 word0123 word0456 word0789\"}"}' \
  --profile account-b \
  response.json

cat response.json
```

### Test Presign URL (Account B)

**Note**: This function requires API Gateway authorizer context. Direct Lambda invocation will return 401.

**Via API Gateway (recommended):**
```bash
# First, validate code to get authorization
# Then call presign URL endpoint with Authorization header

curl -X POST https://api-account-b.execute-api.region.amazonaws.com/presign-url \
  -H "Authorization: word0001 word0023 word0456 word0789 word0123 word0456 word0789 word0123 word0456 word0789" \
  -H "Content-Type: application/json" \
  -d '{"action": "getPresignedUrl", "contentType": "application/pdf", "filename": "test.pdf"}'
```

**Direct Lambda (for testing only - requires simulating authorizer context):**
```bash
aws lambda invoke \
  --function-name file-whitelist-presign-url \
  --payload '{
    "requestContext": {"authorizer": {"principalId": "123"}},
    "body": "{\"action\": \"getPresignedUrl\", \"contentType\": \"application/pdf\", \"filename\": \"test.pdf\"}"
  }' \
  --profile account-b \
  response.json
```

**Multipart upload:**
```bash
# 1. Create multipart upload
aws lambda invoke \
  --function-name file-whitelist-presign-url \
  --payload '{
    "requestContext": {"authorizer": {"principalId": "123"}},
    "body": "{\"action\": \"createMultipartUpload\", \"contentType\": \"application/pdf\", \"filename\": \"large.pdf\"}"
  }' \
  --profile account-b \
  response.json

# 2. Get signed URL for part 1
aws lambda invoke \
  --function-name file-whitelist-presign-url \
  --payload '{
    "requestContext": {"authorizer": {"principalId": "123"}},
    "body": "{\"action\": \"getSignedUrlForPart\", \"key\": \"uploads/123/large.pdf\", \"uploadId\": \"...\", \"partNumber\": 1}"
  }' \
  --profile account-b \
  response.json

# 3. Complete multipart upload
aws lambda invoke \
  --function-name file-whitelist-presign-url \
  --payload '{
    "requestContext": {"authorizer": {"principalId": "123"}},
    "body": "{\"action\": \"completeMultipartUpload\", \"key\": \"uploads/123/large.pdf\", \"uploadId\": \"...\", \"parts\": [{\"PartNumber\": 1, \"ETag\": \"...\"}]}"
  }' \
  --profile account-b \
  response.json
```

## Security Considerations

1. **KMS Keys**: Use KMS for all code signing (same key in both accounts or cross-account access)
2. **STS AssumeRole**: More secure than direct presigned URLs (temporary credentials)
3. **No DynamoDB for Validation**: Stateless validation prevents database tampering
4. **Word-Based Codes**: User-friendly but still cryptographically secure
5. **TTL Enforcement**: Hours-based expiration with clock skew tolerance
6. **S3 Bucket**: Block public access, use bucket policies
7. **Rate Limiting**: Implement at API Gateway level
8. **CORS**: Configure appropriately for your frontend

## Important Notes

- **Shared KMS Key**: Must be accessible from both Account A and Account B
- **No Cross-Account DynamoDB**: Authorizer doesn't need DynamoDB access (pure cryptographic)
- **Counter Table**: Only needed in Account A for daily counter
- **Word Format**: Codes are 10 space-separated words (e.g., `word0001 word0023 ...`)
- **TTL Matching**: `CODE_EXPIRY_HOURS` must match in Account A and Account B

## Directory Structure

```
lambda-functions/
├── code_generator/
│   ├── lambda_function.py
│   └── dictionary.py          # Word encoding/decoding
├── authorizer/
│   ├── lambda_function.py
│   └── dictionary.py          # Word decoding
├── presign_url/
│   └── lambda_function.py     # STS AssumeRole + multipart
├── events/                     # Example event files
├── requirements.txt
└── README.md
```

## Troubleshooting

### Code Generation Fails
- Check DynamoDB table exists in Account A
- Verify Lambda role has DynamoDB UpdateItem permission
- Check KMS_KEY_ID is correct and has GenerateMac permission

### Authorization Fails
- Verify words format is correct (10 space-separated words)
- Check KMS key is accessible from Account B
- Ensure CODE_EXPIRY_HOURS matches in both accounts
- Check code hasn't expired (within TTL window)

### Presigned URL Fails
- **401 Unauthorized**: Missing authorizer context - ensure API Gateway is configured with Lambda Authorizer
- Check S3 bucket exists in Account B
- Verify MINIMAL_S3_ROLE_ARN is correct
- Ensure Lambda role can AssumeRole
- Check bucket name is correct
- **Direct invocation**: This function cannot be called directly - must go through API Gateway

## License

This code is provided as-is for production use. Ensure proper security reviews before deploying to production environments.

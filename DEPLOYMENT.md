# Deployment Guide

## Quick Start

### Prerequisites
- AWS CLI configured with appropriate credentials
- AWS SAM CLI installed (`pip install aws-sam-cli`)
- Docker installed and running (for local testing)

### Step 1: Configure Parameters

Before deploying, decide on your configuration:

**Option A: Use HMAC (Simpler, for development)**
- Set `UseKMS: false`
- Set `HMACSecret` to a strong random string (store in Secrets Manager for production)

**Option B: Use KMS (Recommended for production)**
- Set `UseKMS: true`
- SAM will create a KMS key automatically, or provide existing key ID

### Step 2: Deploy with SAM

```bash
# Guided deployment (first time)
sam deploy --guided

# Or with specific parameters
sam deploy \
  --stack-name file-whitelist-stack \
  --parameter-overrides \
    Environment=prod \
    S3BucketName=my-file-upload-bucket \
    UseKMS=true \
    HMACSecret=your-secret-here \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

### Step 3: Configure Secrets (Production)

**If using HMAC:**
1. Store `HMAC_SECRET` in AWS Secrets Manager:
   ```bash
   aws secretsmanager create-secret \
     --name file-whitelist/hmac-secret \
     --secret-string "your-secret-here"
   ```
2. Update Lambda environment variables to retrieve from Secrets Manager

**If using KMS:**
- SAM creates the key automatically
- Key ARN will be in CloudFormation outputs

### Step 4: Test Deployment

```bash
# Get API Gateway URL from outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name file-whitelist-stack \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiGatewayUrl`].OutputValue' \
  --output text)

# Test code generation
curl -X POST $API_URL/generate-code
```

## Manual Resource Creation

If not using SAM, create resources manually:

### DynamoDB Table
```bash
aws dynamodb create-table \
  --table-name file-whitelist-codes \
  --attribute-definitions AttributeName=code,AttributeType=S \
  --key-schema AttributeName=code,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --time-to-live-specification Enabled=true,AttributeName=ttl
```

### S3 Bucket
```bash
aws s3 mb s3://your-file-upload-bucket
aws s3api put-bucket-encryption \
  --bucket your-file-upload-bucket \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'
```

### KMS Key (if using)
```bash
aws kms create-key \
  --description "File whitelist code signing" \
  --key-usage SIGN_VERIFY \
  --key-spec RSA_2048
```

## Environment Variables Summary

| Variable | Code Generator | Authorizer | Presign URL | Required |
|----------|----------------|-----------|-------------|----------|
| `DYNAMODB_TABLE_NAME` | ✅ | ✅ | ❌ | Yes |
| `USE_KMS` | ✅ | ✅ | ❌ | No (default: false) |
| `KMS_KEY_ID` | ✅* | ✅* | ❌ | If USE_KMS=true |
| `HMAC_SECRET` | ✅* | ✅* | ❌ | If USE_KMS=false |
| `CODE_EXPIRY_MINUTES` | ✅ | ❌ | ❌ | No (default: 60) |
| `S3_BUCKET_NAME` | ❌ | ❌ | ✅ | Yes |
| `PRESIGNED_URL_EXPIRY_SECONDS` | ❌ | ❌ | ✅ | No (default: 3600) |
| `ALLOWED_CONTENT_TYPES` | ❌ | ❌ | ✅ | No (default: */*) |
| `MAX_FILE_SIZE_MB` | ❌ | ❌ | ✅ | No (default: 100) |

## Troubleshooting

### Lambda cannot access DynamoDB
- Check IAM role permissions
- Verify table name in environment variables
- Check VPC configuration (if Lambda is in VPC)

### KMS signing fails
- Verify key has `SIGN_VERIFY` usage
- Check Lambda role has `kms:Sign` permission
- Verify key ID/ARN is correct

### Presigned URL generation fails
- Check S3 bucket exists
- Verify bucket name in environment variables
- Ensure Lambda role has S3 `PutObject` permission

### Authorization fails
- Verify code hasn't expired
- Check signature matches stored signature
- Ensure DynamoDB item exists and state is "active"
- Verify HMAC_SECRET or KMS_KEY_ID matches code generator

## Production Checklist

- [ ] Use KMS instead of HMAC
- [ ] Store secrets in AWS Secrets Manager
- [ ] Enable CloudWatch Logs retention
- [ ] Set up CloudWatch Alarms
- [ ] Configure API Gateway rate limiting
- [ ] Set up WAF rules (if needed)
- [ ] Enable S3 bucket versioning
- [ ] Configure S3 lifecycle policies
- [ ] Set up backup/restore procedures
- [ ] Enable DynamoDB Point-in-Time Recovery
- [ ] Configure CORS appropriately
- [ ] Set up monitoring dashboards
- [ ] Document incident response procedures


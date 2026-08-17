#!/usr/bin/env bash
# Tears down every AWS resource provisioned for Harvest Convoy (ADR-006).
# Run this after judging is done to stop the small standing cost
# (~$2/month worst case, per ADR-006's provisioning table).
#
# Safe to re-run: every command tolerates "already deleted" by continuing
# past errors (set +e per block) rather than aborting partway through.
#
# Does NOT touch: CloudWatch Transaction Search / X-Ray trace segment
# destination (account-level setting, shared by any future AgentCore work
# in this account -- leaving it on costs nothing) or the aws/spans log
# group (AWS-managed, not something this project created directly).
#
# Usage:
#   bash scripts/teardown.sh            # asks for confirmation first
#   bash scripts/teardown.sh --yes      # no confirmation

set -u
REGION="ap-south-1"
ACCOUNT_ID="675613597178"

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will permanently delete:"
  echo "  - EventBridge Schedule: harvest-convoy-daily-watch"
  echo "  - Lambda function: harvest-convoy-watcher-invoker"
  echo "  - AgentCore Runtime: harvest_convoy_watcher-7DW91DHIBA"
  echo "  - DynamoDB table: harvest_convoy (ALL DATA)"
  echo "  - S3 object: s3://bedrock-agentcore-code-675613597178-ap-south-1/harvest-convoy-watcher/deployment_package.zip"
  echo "  - S3 bucket: bedrock-agentcore-code-675613597178-ap-south-1 (if empty after the above)"
  echo "  - IAM roles: harvest-convoy-watcher-execution, harvest-convoy-scheduler-invoke,"
  echo "               harvest-convoy-watcher-invoker-lambda"
  echo
  read -r -p "Type 'yes' to continue: " confirm
  [[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

echo "== EventBridge Schedule =="
aws scheduler delete-schedule --name harvest-convoy-daily-watch --region "$REGION" 2>&1 || true

echo "== Lambda function =="
aws lambda delete-function --function-name harvest-convoy-watcher-invoker --region "$REGION" 2>&1 || true

echo "== AgentCore Runtime =="
aws bedrock-agentcore-control delete-agent-runtime \
  --agent-runtime-id harvest_convoy_watcher-7DW91DHIBA --region "$REGION" 2>&1 || true

echo "== DynamoDB table (all seeded data) =="
aws dynamodb delete-table --table-name harvest_convoy --region "$REGION" 2>&1 || true

echo "== S3 artifact =="
aws s3 rm "s3://bedrock-agentcore-code-675613597178-ap-south-1/harvest-convoy-watcher/deployment_package.zip" --region "$REGION" 2>&1 || true
aws s3 rb "s3://bedrock-agentcore-code-675613597178-ap-south-1" --region "$REGION" 2>&1 || true

echo "== IAM roles (inline policies then the roles) =="
for role in harvest-convoy-watcher-execution harvest-convoy-scheduler-invoke harvest-convoy-watcher-invoker-lambda; do
  for policy in $(aws iam list-role-policies --role-name "$role" --region "$REGION" --query "PolicyNames[]" --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$role" --policy-name "$policy" --region "$REGION" 2>&1 || true
  done
  aws iam delete-role --role-name "$role" --region "$REGION" 2>&1 || true
done

echo
echo "Teardown complete. Verify nothing billable remains:"
echo "  aws bedrock-agentcore-control list-agent-runtimes --region $REGION"
echo "  aws dynamodb list-tables --region $REGION"
echo "  aws lambda list-functions --region $REGION --query \"Functions[?starts_with(FunctionName,'harvest-convoy')]\""
echo "  aws scheduler list-schedules --region $REGION"
echo "  aws s3 ls --region $REGION"
echo "  aws iam list-roles --query \"Roles[?starts_with(RoleName,'harvest-convoy')].RoleName\""

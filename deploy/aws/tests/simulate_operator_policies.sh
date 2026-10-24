#!/usr/bin/env bash
# Checks each enabled operator role of an applied stack with IAM policy
# simulation: it may send only its own documents to this instance and read the
# ops-output bucket, and everything else is an explicit deny (so it holds even
# with a broader policy attached). Run it with all three roles on so the
# cross-role denials are covered.
#
#   deploy/aws/tests/simulate_operator_policies.sh [MODULE_DIR]
#
# Needs terraform, jq, and AWS credentials in the owner's account allowed
# iam:SimulatePrincipalPolicy.
set -Eeuo pipefail

out=$(terraform -chdir="${1:-$(dirname "$0")/..}" output -json)
get() { jq -r "$1" <<<"$out"; }

instance=$(get .instance_arn.value)
IFS=: read -r _ partition _ region account _ <<<"$instance"
prefix=$(get .ssm_prefix.value)
bucket=$(get .backup_bucket.value)
output_bucket=$(get .ops_output_bucket.value)
kms=$(get .kms_key_arn.value)
other_instance="arn:$partition:ec2:$region:$account:instance/i-00000000000000000"
run_shell="arn:$partition:ssm:$region::document/AWS-RunShellScript"
param="arn:$partition:ssm:$region:$account:parameter$prefix/postgres_password"
param_path="arn:$partition:ssm:$region:$account:parameter$prefix"
prefix_arn=$param_path

[[ $(get '.operator_role_arns.value | length') -gt 0 ]] \
    || { echo "no operator roles are enabled; turn them on and re-apply" >&2; exit 2; }

failed=0
expect() {
    local want=$1 role=$2 action=$3 resource=$4 got
    got=$(aws iam simulate-principal-policy --policy-source-arn "$role" \
        --action-names "$action" --resource-arns "$resource" \
        --query 'EvaluationResults[0].EvalDecision' --output text)
    if [[ $want == allow && $got != allowed || $want == deny && $got != explicitDeny ]]; then
        echo "FAIL ${role##*/} $action $resource: $got (want $want)"
        failed=1
    else
        echo "ok   ${role##*/} $action $resource: $got"
    fi
}

while read -r name role; do
    own=$(get ".operator_actions.value[\"$name\"][]")
    expect allow "$role" ssm:SendCommand "$instance"
    expect deny "$role" ssm:SendCommand "$other_instance"
    expect deny "$role" ssm:SendCommand "$run_shell"
    while read -r action doc; do
        if grep -qx -- "$action" <<<"$own"; then
            expect allow "$role" ssm:SendCommand "$doc"
        else
            expect deny "$role" ssm:SendCommand "$doc"
        fi
        expect deny "$role" ssm:UpdateDocument "$doc"
    done < <(get '.document_arns.value | to_entries[] | "\(.key) \(.value)"')
    expect deny "$role" ssm:CreateDocument "arn:$partition:ssm:$region:$account:document/memory-new"
    expect deny "$role" ssm:StartSession "$instance"
    expect deny "$role" kms:Decrypt "$kms"
    expect deny "$role" ssm:GetParameter "$param"
    expect deny "$role" ssm:GetParametersByPath "$param_path"
    expect deny "$role" ssm:PutParameter "$prefix_arn/release_signers"
    expect deny "$role" ssm:GetCommandInvocation "*"
    expect deny "$role" ssm:ListCommandInvocations "*"
    expect deny "$role" ssm:ListCommands "*"
    expect deny "$role" ec2:GetConsoleOutput "$instance"
    expect deny "$role" ec2:ModifyInstanceAttribute "$instance"
    expect deny "$role" logs:GetLogEvents "*"
    expect deny "$role" s3:GetObject "arn:$partition:s3:::$bucket/memory/databases/latest"
    expect deny "$role" s3:ListBucket "arn:$partition:s3:::$bucket"
    expect allow "$role" s3:GetObject "arn:$partition:s3:::$output_bucket/ops/cmd/out"
    expect allow "$role" s3:ListBucket "arn:$partition:s3:::$output_bucket"
    expect deny "$role" iam:PassRole "arn:$partition:iam::$account:role/memory-instance"
    expect "$([[ $name == maintain ]] && echo allow || echo deny)" "$role" ec2:RebootInstances "$instance"
done < <(get '.operator_role_arns.value | to_entries[] | "\(.key) \(.value)"')

# Operators choose the output bucket, so the instance must refuse to write
# command output into the backups.
instance_role=$(get .instance_role_arn.value)
expect deny "$instance_role" s3:PutObject "arn:$partition:s3:::$bucket/x/cmd/i-0/awsrunShellScript/s/stdout"
expect allow "$instance_role" s3:PutObject "arn:$partition:s3:::$bucket/memory/databases/postgres.enc"
expect allow "$instance_role" s3:PutObject "arn:$partition:s3:::$output_bucket/ops/cmd/i-0/awsrunShellScript/s/stdout"

exit "$failed"

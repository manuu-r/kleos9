#!/usr/bin/env bash
set -euo pipefail
: "${AWS_REGION:?}"
: "${ECS_CLUSTER:?}"
: "${SIMULATION_REPOSITORY:?}"
: "${SIMULATION_API_URL:?}"
: "${GITHUB_SHA:?}"
: "${GITHUB_RUN_ID:?}"
: "${GITHUB_RUN_ATTEMPT:?}"
registry="${SIMULATION_REPOSITORY%%/*}"
tag="${GITHUB_SHA:0:16}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$registry"
docker build --platform linux/amd64 -t "$SIMULATION_REPOSITORY:$tag" mock-backend
docker push "$SIMULATION_REPOSITORY:$tag"
repository="${SIMULATION_REPOSITORY#*/}"
digest=$(aws ecr describe-images --repository-name "$repository" --image-ids "imageTag=$tag" --query 'imageDetails[0].imageDigest' --output text)
image="$SIMULATION_REPOSITORY@$digest"
current=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services simulation --query 'services[0].taskDefinition' --output text)
family=$(aws ecs describe-task-definition --task-definition "$current" --query 'taskDefinition.family' --output text)
aws ecs describe-task-definition --task-definition "$family" --query taskDefinition > /tmp/kleos9-simulation-task.json
jq --arg image "$image" '
  del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy,.deregisteredAt)
  | .containerDefinitions |= map(if .name == "simulation" then .image = $image else . end)
' /tmp/kleos9-simulation-task.json > /tmp/kleos9-simulation-release.json
definition=$(aws ecs register-task-definition --cli-input-json file:///tmp/kleos9-simulation-release.json --query taskDefinition.taskDefinitionArn --output text)
jq -n --arg image "$image" --arg old "$current" --arg new "$definition" '{image:$image,previousTaskDefinition:$old,newTaskDefinition:$new,status:"rolling"}' > simulation_deployment_manifest.json
aws ecs update-service --cluster "$ECS_CLUSTER" --service simulation --task-definition "$definition" --desired-count 1 > /dev/null
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services simulation
actual=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services simulation --query 'services[0].taskDefinition' --output text)
if [ "$actual" != "$definition" ]; then
  echo 'Simulation service rolled back instead of reaching the requested revision.' >&2
  exit 1
fi
curl --fail --silent --show-error "$SIMULATION_API_URL/health" > /tmp/kleos9-simulation-health.json
python3 -c 'import json; d=json.load(open("/tmp/kleos9-simulation-health.json")); assert d["status"]=="ok" and d["mode"]=="mock" and d["realIntegrations"] is False'
jq '.status = "complete"' simulation_deployment_manifest.json > /tmp/kleos9-simulation-manifest.json
mv /tmp/kleos9-simulation-manifest.json simulation_deployment_manifest.json

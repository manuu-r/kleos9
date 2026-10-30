"""Manual ECS release orchestration. Database access stays inside a VPC task."""
import argparse
import base64
import copy
import json
import re
import subprocess
import time
from pathlib import Path

TASK_FIELDS = {
    "family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions",
    "volumes", "placementConstraints", "requiresCompatibilities", "cpu", "memory",
    "pidMode", "ipcMode", "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage",
    "runtimePlatform", "enableFaultInjection",
}


def release_definition(definition, image, version):
    result = {key: copy.deepcopy(value) for key, value in definition.items() if key in TASK_FIELDS}
    control = next(item for item in result["containerDefinitions"] if item["name"] == "control")
    control["image"] = image
    environment = {item["name"]: item["value"] for item in control.get("environment", [])}
    environment["KLEOS_DBOS_APPLICATION_VERSION"] = version
    control["environment"] = [{"name": key, "value": value} for key, value in sorted(environment.items())]
    return result


def application_version(definition):
    control = next(item for item in definition["containerDefinitions"] if item["name"] == "control")
    return next((item["value"] for item in control.get("environment", [])
                 if item["name"] == "KLEOS_DBOS_APPLICATION_VERSION"), "0.1.0")


def completed_task(task):
    if task.get("lastStatus") != "STOPPED":
        return False
    control = next((item for item in task.get("containers", []) if item["name"] == "control"), {})
    if control.get("exitCode") != 0:
        raise RuntimeError(f"One-off task failed; inspect its CloudWatch logs: {task['taskArn']}")
    return True


class Release:
    def __init__(self, args, ecs=None, ecr=None):
        import boto3
        self.args = args
        self.ecs = ecs or boto3.client("ecs", region_name=args.region)
        self.ecr = ecr or boto3.client("ecr", region_name=args.region)
        self.services = {}
        self.configurations = {}
        self.manifest = {"version": args.version, "cluster": args.cluster, "status": "preparing",
                         "previousDefinitions": {}, "newDefinitions": {}, "drainers": [], "oneOffTasks": []}

    def save(self):
        Path(self.args.manifest).write_text(json.dumps(self.manifest, indent=2) + "\n")

    def image(self):
        match = re.fullmatch(r"([0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com)/([a-z0-9_./-]+)", self.args.repository)
        if not match:
            raise ValueError("repository must be a private ECR repository URI")
        registry, repository = match.groups()
        credentials = self.ecr.get_authorization_token()["authorizationData"][0]
        username, password = base64.b64decode(credentials["authorizationToken"]).decode().split(":", 1)
        subprocess.run(["docker", "login", "--username", username, "--password-stdin", registry],
                       input=password, text=True, check=True, capture_output=True)
        image = f"{self.args.repository}:{self.args.version[:16]}-{self.args.release_id}"
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "-f", "backend/docker/Dockerfile",
                        "-t", image, "backend"], check=True)
        subprocess.run(["docker", "push", image], check=True)
        digest = self.ecr.describe_images(repositoryName=repository, imageIds=[
            {"imageTag": image.rsplit(":", 1)[1]}])["imageDetails"][0]["imageDigest"]
        self.manifest["image"] = f"{self.args.repository}@{digest}"
        self.save()
        return self.manifest["image"]

    def discover(self):
        names = ["api", "engineering", "connectors"]
        response = self.ecs.describe_services(cluster=self.args.cluster, services=names)
        if response.get("failures") or len(response.get("services", [])) != 3:
            raise RuntimeError("Provision the api, engineering and connectors ECS services first")
        definitions = {}
        for service in response["services"]:
            name = service["serviceName"]
            if service["status"] != "ACTIVE" or (service["desiredCount"] == 0 and not self.args.initial_deployment):
                raise RuntimeError("Inactive services require the explicit initial-deployment option")
            if service["runningCount"] != service["desiredCount"] or len(service.get("deployments", [])) != 1:
                raise RuntimeError("Existing services must be stable before deployment")
            self.services[name] = service
            definitions[name] = self.ecs.describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
            # Terraform can register updated environment/IAM configuration without rolling services.
            self.configurations[name] = self.ecs.describe_task_definition(
                taskDefinition=definitions[name]["family"])["taskDefinition"]
            self.manifest["previousDefinitions"][name] = service["taskDefinition"]
        self.save()
        return definitions

    def one_off(self, role, definition, command, marker):
        response = self.ecs.run_task(
            cluster=self.args.cluster, taskDefinition=definition, launchType="FARGATE", count=1,
            networkConfiguration=self.services[role]["networkConfiguration"],
            clientToken=f"{self.args.release_id}-{marker}"[:64],
            overrides={"containerOverrides": [{"name": "control", "command": command}]},
            enableExecuteCommand=False,
            tags=[{"key": "kleos-release", "value": self.args.version}, {"key": "kleos-release-purpose", "value": marker}],
        )
        if response.get("failures") or len(response.get("tasks", [])) != 1:
            raise RuntimeError(f"ECS did not start the {marker} task")
        arn = response["tasks"][0]["taskArn"]
        self.manifest["oneOffTasks"].append({"purpose": marker, "taskArn": arn})
        self.save()
        return arn

    def wait_task(self, arn, seconds, running=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            tasks = self.ecs.describe_tasks(cluster=self.args.cluster, tasks=[arn]).get("tasks", [])
            if tasks:
                if running and tasks[0].get("lastStatus") == "RUNNING":
                    return
                if completed_task(tasks[0]):
                    if running:
                        raise RuntimeError("Compatibility drainer exited before rollout")
                    return
            time.sleep(5)
        raise TimeoutError(f"One-off outcome uncertain; inspect task before retrying: {arn}")

    def ensure_drainers(self):
        arns = self.manifest["drainers"]
        if not arns:
            return
        result = self.ecs.describe_tasks(cluster=self.args.cluster, tasks=arns)
        if result.get("failures") or len(result.get("tasks", [])) != len(arns) or any(
                task.get("lastStatus") != "RUNNING" for task in result.get("tasks", [])):
            raise RuntimeError("An old-version drainer stopped; restore compatible workers before proceeding")

    def wait_service(self, name, target):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            self.ensure_drainers()
            service = self.ecs.describe_services(cluster=self.args.cluster, services=[name])["services"][0]
            primary = next((item for item in service.get("deployments", []) if item.get("status") == "PRIMARY"), {})
            if primary.get("rolloutState") == "FAILED":
                raise RuntimeError(f"{name} deployment failed; compatibility drainers retained")
            if len(service.get("deployments", [])) == 1 and service["runningCount"] == service["desiredCount"]:
                if service["taskDefinition"] != target:
                    raise RuntimeError(f"{name} rolled back instead of reaching the requested version")
                return
            time.sleep(10)
        raise TimeoutError(f"{name} did not become healthy; compatibility drainers retained")

    def drain(self, versions, phase):
        if not versions:
            return
        arn = self.one_off("engineering", self.manifest["newDefinitions"]["engineering"],
                           ["python", "/opt/kleos/deploy.py", "drain-check", "--versions", *versions,
                            "--timeout", str(self.args.drain_timeout)], f"drain-{phase}")
        self.wait_task(arn, self.args.drain_timeout + 300)
        self.ensure_drainers()

    def run(self):
        definitions = self.discover()
        image = self.image()
        for name, definition in definitions.items():
            response = self.ecs.register_task_definition(**release_definition(
                self.configurations.get(name, definition), image, self.args.version))
            self.manifest["newDefinitions"][name] = response["taskDefinition"]["taskDefinitionArn"]
        self.save()
        migration = self.one_off("api", self.manifest["newDefinitions"]["api"], ["alembic", "upgrade", "head"], "migration")
        self.wait_task(migration, 900)
        versions = sorted({application_version(definitions[name]) for name in ("engineering", "connectors")
                           if self.services[name]["desiredCount"] > 0 and application_version(definitions[name]) != self.args.version})
        for name in ("engineering", "connectors"):
            if application_version(definitions[name]) not in versions:
                continue
            arn = self.one_off(name, self.manifest["previousDefinitions"][name],
                               ["python", "-m", "kleos.worker", "--kind", name, "--drain"], f"compat-{name}")
            self.manifest["drainers"].append(arn)
            self.save()
            self.wait_task(arn, 300, running=True)
        self.drain(versions, "before")
        self.manifest["status"] = "rolling"
        self.save()
        for name in ("engineering", "connectors", "api"):
            target = self.manifest["newDefinitions"][name]
            self.ecs.update_service(cluster=self.args.cluster, service=name, taskDefinition=target,
                                    desiredCount=max(1, self.services[name]["desiredCount"]))
            self.wait_service(name, target)
        import httpx
        health = httpx.get(self.args.api_url.rstrip("/") + "/api/v1/health", timeout=30, follow_redirects=False)
        health.raise_for_status()
        if health.json().get("status") != "ok":
            raise RuntimeError("Public API health check failed")
        self.drain(versions, "after")
        for arn in self.manifest["drainers"]:
            self.ecs.stop_task(cluster=self.args.cluster, task=arn, reason="Old DBOS version drained after healthy deployment")
        self.manifest["status"] = "complete"
        self.save()
        print(f"Deployment complete: {self.manifest['image']}")


def drain_check(versions, timeout):
    from dbos import DBOSClient

    from kleos.config import get_settings
    client = DBOSClient(system_database_url=get_settings().dbos_database_url, application_name="kleos")
    deadline, empty_since = time.monotonic() + timeout, None
    try:
        while time.monotonic() < deadline:
            active = client.list_workflows(app_version=versions, status=["PENDING", "ENQUEUED", "DELAYED"],
                                           limit=1, load_input=False, load_output=False)
            if active:
                empty_since = None
            else:
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since >= 30:
                    print("Old DBOS versions drained for 30 seconds")
                    return
            time.sleep(5)
        raise TimeoutError("Old workflows remain active; compatible workers must be retained")
    finally:
        client.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    release = commands.add_parser("release")
    release.add_argument("--region", default="us-east-1")
    for name in ("cluster", "repository", "version", "release-id", "api-url"):
        release.add_argument(f"--{name}", required=True)
    release.add_argument("--manifest", default="deployment_manifest.json")
    release.add_argument("--initial-deployment", action="store_true")
    release.add_argument("--drain-timeout", type=int, default=1800)
    check = commands.add_parser("drain-check")
    check.add_argument("--versions", nargs="+", required=True)
    check.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.command == "drain-check":
        return drain_check(args.versions, args.timeout)
    if not re.fullmatch(r"[a-f0-9]{40}", args.version):
        parser.error("--version must be a full Git commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.release_id):
        parser.error("--release-id must be a short unique identifier")
    if not args.api_url.startswith("https://"):
        parser.error("--api-url must use HTTPS")
    deployment = Release(args)
    try:
        deployment.run()
    except Exception:
        deployment.manifest["status"] = "requires_reconciliation"
        deployment.save()
        print("Deployment stopped. Consult deployment_manifest.json; compatibility workers were retained.")
        raise


if __name__ == "__main__":
    main()

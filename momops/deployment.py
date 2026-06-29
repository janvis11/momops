"""Deployment orchestration for real AWS resources."""

from __future__ import annotations

import asyncio
import logging
import secrets
import string
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from momops.models import (
    ArchitectureBlueprint,
    AWSService,
    DeployedApp,
    DeployEvent,
    DeployStatus,
)
from momops.providers.aws import AWSProvider
from momops.safety import validate_blueprint

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError as err:  # pragma: no cover
    raise ImportError("boto3 is required for MomOps deployment") from err

logger = logging.getLogger(__name__)


class DeploymentError(Exception):
    """Raised on unrecoverable deployment failure."""


@dataclass
class ProvisionedResource:
    """Enough information to report and later roll back an AWS resource."""

    step_name: str
    resource_type: str
    resource_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeploymentContext:
    """Shared AWS IDs created during one deployment run."""

    app_id: str
    region: str
    vpc_id: str | None = None
    subnet_ids: list[str] = field(default_factory=list)
    public_subnet_ids: list[str] = field(default_factory=list)
    security_group_ids: dict[str, str] = field(default_factory=dict)
    instance_ids: list[str] = field(default_factory=list)
    target_group_arn: str | None = None
    load_balancer_arn: str | None = None
    load_balancer_dns: str | None = None
    rds_instance_id: str | None = None
    bucket_names: list[str] = field(default_factory=list)


class AWSProvisioner:
    """Translate MomOps deploy steps into concrete AWS API calls."""

    def __init__(self, blueprint: ArchitectureBlueprint, app_id: str) -> None:
        self.blueprint = blueprint
        self.ctx = DeploymentContext(app_id=app_id, region=blueprint.requirement.region)
        self.provider = AWSProvider(region=self.ctx.region)
        self.ec2 = self.provider.client("ec2")
        self.rds = self.provider.client("rds")
        self.elbv2 = self.provider.client("elbv2")
        self.s3 = self.provider.client("s3")
        self.cloudfront = self.provider.client("cloudfront")
        self.cloudwatch = self.provider.client("cloudwatch")
        self.sqs = self.provider.client("sqs")
        self.ecs = self.provider.client("ecs")
        self.secretsmanager = self.provider.client("secretsmanager")
        self.elasticache = self.provider.client("elasticache")
        self.apigatewayv2 = self.provider.client("apigatewayv2")
        self.ssm = self.provider.client("ssm")

    def provision(self, step_name: str) -> list[ProvisionedResource]:
        """Run the AWS handler or handlers for a recipe step."""
        normalized = step_name.lower()
        handled = False
        resources: list[ProvisionedResource] = []

        if "vpc" in normalized:
            handled = True
            resources.extend(self.ensure_vpc())
        if "security group" in normalized:
            handled = True
            resources.extend(self.ensure_security_groups())
        if "s3" in normalized:
            handled = True
            resources.extend(self.ensure_s3())
        if "cdn" in normalized or "cloudfront" in normalized:
            handled = True
            resources.extend(self.ensure_cloudfront())
        if "database" in normalized:
            handled = True
            resources.extend(self.ensure_database(step_name))
        if normalized in {"compute", "services"}:
            handled = True
            resources.extend(self.ensure_compute(step_name))
        if "load balancer" in normalized or "alb" in normalized:
            handled = True
            resources.extend(self.ensure_load_balancer(step_name))
        if "queue" in normalized or "sqs" in normalized:
            handled = True
            resources.extend(self.ensure_sqs(step_name))
        if "ecs" in normalized:
            handled = True
            resources.extend(self.ensure_ecs_cluster(step_name))
        if "secret" in normalized:
            handled = True
            resources.extend(self.ensure_secret(step_name))
        if "redis" in normalized:
            handled = True
            resources.extend(self.ensure_redis(step_name))
        if "api gateway" in normalized:
            handled = True
            resources.extend(self.ensure_api_gateway(step_name))
        if "monitoring" in normalized:
            handled = True
            resources.extend(self.ensure_monitoring(step_name))
        if "ssl certificate" in normalized:
            handled = True
            resources.extend(self.ensure_certificate(step_name))
        if "route53" in normalized:
            handled = True
            resources.extend(self.ensure_route53_ready(step_name))

        if not handled:
            raise DeploymentError(f"No AWS provisioning handler exists for step: {step_name}")
        return resources

    def endpoint(self) -> str | None:
        """Return the best reachable endpoint created by this deployment."""
        if self.ctx.load_balancer_dns:
            return f"http://{self.ctx.load_balancer_dns}"
        if self.ctx.bucket_names:
            bucket = self.ctx.bucket_names[0]
            return f"https://{bucket}.s3.{self.ctx.region}.amazonaws.com"
        return None

    def rollback_resource(self, resource: ProvisionedResource) -> None:
        """Delete a single AWS resource."""
        if resource.resource_type == "vpc":
            self.ec2.delete_vpc(VpcId=resource.resource_id)
        elif resource.resource_type == "subnet":
            self.ec2.delete_subnet(SubnetId=resource.resource_id)
        elif resource.resource_type == "internet_gateway":
            # Detach from VPC first
            try:
                response = self.ec2.describe_internet_gateways(
                    InternetGatewayIds=[resource.resource_id]
                )
                igw = response["InternetGateways"][0]
                for attachment in igw["Attachments"]:
                    self.ec2.detach_internet_gateway(
                        InternetGatewayId=resource.resource_id, VpcId=attachment["VpcId"]
                    )
            except self.ec2.exceptions.InvalidInternetGatewayID.NotFound:
                # Already detached or deleted
                pass
            self.ec2.delete_internet_gateway(InternetGatewayId=resource.resource_id)
        elif resource.resource_type == "route_table":
            self.ec2.delete_route_table(RouteTableId=resource.resource_id)
        elif resource.resource_type == "route_table_association":
            self.ec2.disassociate_route_table(AssociationId=resource.resource_id)
        elif resource.resource_type == "security_group":
            self.ec2.delete_security_group(GroupId=resource.resource_id)
        elif resource.resource_type == "rds_subnet_group":
            self.rds.delete_db_subnet_group(DBSubnetGroupName=resource.resource_id)
        elif resource.resource_type == "rds_instance":
            self.rds.delete_db_instance(
                DBInstanceIdentifier=resource.resource_id, SkipFinalSnapshot=True
            )
        elif resource.resource_type == "ec2_instance":
            self.ec2.terminate_instances(InstanceIds=[resource.resource_id])
        elif resource.resource_type == "target_group":
            self.elbv2.delete_target_group(TargetGroupArn=resource.resource_id)
        elif resource.resource_type == "load_balancer":
            self.elbv2.delete_load_balancer(LoadBalancerArn=resource.resource_id)
        elif resource.resource_type == "listener":
            self.elbv2.delete_listener(ListenerArn=resource.resource_id)
        elif resource.resource_type == "s3_bucket":
            # Empty bucket if needed
            try:
                self.s3.delete_bucket(Bucket=resource.resource_id)
            except self.s3.exceptions.BucketNotEmpty:
                # Delete all objects
                paginator = self.s3.get_paginator("list_object_v2")
                for page in paginator.paginate(Bucket=resource.resource_id):
                    for obj in page.get("Contents", []):
                        self.s3.delete_object(Bucket=resource.resource_id, Key=obj["Key"])
                # Delete all object versions if versioning was enabled
                paginator = self.s3.get_paginator("list_object_versions")
                for page in paginator.paginate(Bucket=resource.resource_id):
                    for version in page.get("Versions", []):
                        self.s3.delete_object(
                            Bucket=resource.resource_id,
                            Key=version["Key"],
                            VersionId=version["VersionId"],
                        )
                    for delete_marker in page.get("DeleteMarkers", []):
                        self.s3.delete_object(
                            Bucket=resource.resource_id,
                            Key=delete_marker["Key"],
                            VersionId=delete_marker["VersionId"],
                        )
                self.s3.delete_bucket(Bucket=resource.resource_id)
        elif resource.resource_type == "cloudfront_distribution":
            # Disable distribution before deletion
            try:
                self.cloudfront.delete_distribution(Id=resource.resource_id)
            except self.cloudfront.exceptions.InvalidIfMatchVersion:
                # Need to get current config and ETag
                response = self.cloudfront.get_distribution_config(Id=resource.resource_id)
                etag = response["ETag"]
                config = response["DistributionConfig"]
                config["Enabled"] = False
                self.cloudfront.update_distribution(
                    Id=resource.resource_id, DistributionConfig=config, IfMatch=etag
                )
                # Wait a bit for propagation
                time.sleep(1)
                self.cloudfront.delete_distribution(Id=resource.resource_id)
            except self.cloudfront.exceptions.NoSuchDistribution:
                pass
        elif resource.resource_type == "sqs_queue":
            self.sqs.delete_queue(QueueUrl=resource.resource_id)
        elif resource.resource_type == "ecs_cluster":
            self.ecs.delete_cluster(cluster=resource.resource_id)
        elif resource.resource_type == "secret":
            self.secretsmanager.delete_secret(
                SecretId=resource.resource_id, ForceDeleteWithoutRecovery=True
            )
        elif resource.resource_type == "elasticache_subnet_group":
            self.elasticache.delete_cache_subnet_group(CacheSubnetGroupName=resource.resource_id)
        elif resource.resource_type == "elasticache_cluster":
            self.elasticache.delete_cache_cluster(CacheClusterId=resource.resource_id)
        elif resource.resource_type == "api_gateway":
            self.apigatewayv2.delete_api(ApiId=resource.resource_id)
        elif resource.resource_type == "cloudwatch_dashboard":
            self.cloudwatch.delete_dashboard(DashboardName=resource.resource_id)
        elif resource.resource_type == "certificate":
            try:
                acm = self.provider.client("acm")
                acm.delete_certificate(CertificateArn=resource.resource_id)
            except Exception as e:
                logger.warning("Failed to delete certificate %s: %s", resource.resource_id, e)
        elif resource.resource_type == "route53_zone":
            # We did not create the hosted zone, so do not delete it
            logger.info(
                "Skipping deletion of Route53 zone %s because we did not create it",
                resource.resource_id,
            )
        else:
            logger.warning("Unknown resource type for rollback: %s", resource.resource_type)

    def ensure_vpc(self) -> list[ProvisionedResource]:
        if self.ctx.vpc_id:
            return []

        cidr = self._service_config("VPC").get("cidr", "10.0.0.0/16")
        vpc_id = self.ec2.create_vpc(CidrBlock=cidr)["Vpc"]["VpcId"]
        self.ctx.vpc_id = vpc_id
        self.ec2.create_tags(Resources=[vpc_id], Tags=self._tags("vpc"))
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

        resources = [ProvisionedResource("VPC", "vpc", vpc_id)]
        zones = self.ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )["AvailabilityZones"]
        az_names = [zone["ZoneName"] for zone in zones[:2]] or [f"{self.ctx.region}a"]
        subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24"]

        for idx, az_name in enumerate(az_names):
            subnet_id = self.ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=subnet_cidrs[idx],
                AvailabilityZone=az_name,
            )["Subnet"]["SubnetId"]
            self.ctx.subnet_ids.append(subnet_id)
            self.ctx.public_subnet_ids.append(subnet_id)
            self.ec2.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={"Value": True},
            )
            self.ec2.create_tags(Resources=[subnet_id], Tags=self._tags(f"subnet-{idx + 1}"))
            resources.append(ProvisionedResource("VPC", "subnet", subnet_id))

        igw_id = self.ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
        self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        self.ec2.create_tags(Resources=[igw_id], Tags=self._tags("igw"))
        resources.append(ProvisionedResource("VPC", "internet_gateway", igw_id))

        route_table_id = self.ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
        self.ec2.create_tags(Resources=[route_table_id], Tags=self._tags("public-routes"))
        self.ec2.create_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=igw_id,
        )
        resources.append(ProvisionedResource("VPC", "route_table", route_table_id))
        for subnet_id in self.ctx.public_subnet_ids:
            association_id = self.ec2.associate_route_table(
                RouteTableId=route_table_id,
                SubnetId=subnet_id,
            )["AssociationId"]
            resources.append(ProvisionedResource("VPC", "route_table_association", association_id))
        return resources

    def ensure_security_groups(self) -> list[ProvisionedResource]:
        self.ensure_vpc()
        if self.ctx.security_group_ids:
            return []

        vpc_id = self._require(self.ctx.vpc_id, "VPC must exist before security groups")
        resources: list[ProvisionedResource] = []

        web_sg = self.ec2.create_security_group(
            GroupName=self._name("web-sg"),
            Description="MomOps web/load-balancer security group",
            VpcId=vpc_id,
        )["GroupId"]
        self.ec2.authorize_security_group_ingress(
            GroupId=web_sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )
        self.ctx.security_group_ids["web"] = web_sg
        resources.append(ProvisionedResource("Security Groups", "security_group", web_sg))

        app_sg = self.ec2.create_security_group(
            GroupName=self._name("app-sg"),
            Description="MomOps application security group",
            VpcId=vpc_id,
        )["GroupId"]
        self.ec2.authorize_security_group_ingress(
            GroupId=app_sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "UserIdGroupPairs": [{"GroupId": web_sg}],
                }
            ],
        )
        self.ctx.security_group_ids["app"] = app_sg
        resources.append(ProvisionedResource("Security Groups", "security_group", app_sg))

        db_sg = self.ec2.create_security_group(
            GroupName=self._name("db-sg"),
            Description="MomOps database security group",
            VpcId=vpc_id,
        )["GroupId"]
        self.ec2.authorize_security_group_ingress(
            GroupId=db_sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "UserIdGroupPairs": [{"GroupId": app_sg}],
                }
            ],
        )
        self.ctx.security_group_ids["db"] = db_sg
        resources.append(ProvisionedResource("Security Groups", "security_group", db_sg))
        return resources

    def ensure_database(self, step_name: str) -> list[ProvisionedResource]:
        rds_service = self._first_service("RDS")
        if rds_service is None:
            return []

        self.ensure_vpc()
        self.ensure_security_groups()
        if self.ctx.rds_instance_id:
            return []

        db_id = self._name("db")
        subnet_group = self._name("db-subnets")
        engine = str(rds_service.config.get("engine", "postgres"))
        instance_class = rds_service.instance_type or "db.t3.micro"

        self.rds.create_db_subnet_group(
            DBSubnetGroupName=subnet_group,
            DBSubnetGroupDescription="MomOps managed database subnets",
            SubnetIds=self.ctx.subnet_ids,
            Tags=self._tags("db-subnet-group"),
        )
        self.rds.create_db_instance(
            DBInstanceIdentifier=db_id,
            AllocatedStorage=20,
            DBInstanceClass=instance_class,
            Engine=engine,
            MasterUsername="momops_admin",
            MasterUserPassword=self._password(),
            VpcSecurityGroupIds=[self.ctx.security_group_ids["db"]],
            DBSubnetGroupName=subnet_group,
            BackupRetentionPeriod=7,
            StorageEncrypted=True,
            MultiAZ=bool(rds_service.config.get("multi_az", False)),
            PubliclyAccessible=False,
            Tags=self._tags("database"),
        )
        self.ctx.rds_instance_id = db_id
        return [
            ProvisionedResource(step_name, "rds_subnet_group", subnet_group),
            ProvisionedResource(step_name, "rds_instance", db_id, {"skip_final_snapshot": True}),
        ]

    def ensure_compute(self, step_name: str) -> list[ProvisionedResource]:
        ec2_service = self._first_service("EC2")
        if ec2_service is None:
            return []

        self.ensure_vpc()
        self.ensure_security_groups()
        user_data = """#!/bin/bash
mkdir -p /var/www/html
cat > /var/www/html/index.html <<'HTML'
MomOps deployment is live
HTML
nohup python3 -m http.server 80 --directory /var/www/html >/var/log/momops-http.log 2>&1 &
"""
        min_count = int(ec2_service.config.get("min", 1))
        response = self.ec2.run_instances(
            ImageId=self._latest_amazon_linux_ami(),
            InstanceType=ec2_service.instance_type or "t3.micro",
            MinCount=min_count,
            MaxCount=min_count,
            SubnetId=self.ctx.public_subnet_ids[0],
            SecurityGroupIds=[self.ctx.security_group_ids["app"]],
            UserData=user_data,
            TagSpecifications=[{"ResourceType": "instance", "Tags": self._tags("compute")}],
        )
        instance_ids = [instance["InstanceId"] for instance in response["Instances"]]
        self.ctx.instance_ids.extend(instance_ids)
        return [
            ProvisionedResource(step_name, "ec2_instance", instance_id)
            for instance_id in instance_ids
        ]

    def ensure_load_balancer(self, step_name: str) -> list[ProvisionedResource]:
        self.ensure_vpc()
        self.ensure_security_groups()
        resources: list[ProvisionedResource] = []

        if self.ctx.target_group_arn is None:
            target_group = self.elbv2.create_target_group(
                Name=self._short_name("tg"),
                Protocol="HTTP",
                Port=80,
                VpcId=self._require(self.ctx.vpc_id, "VPC is required for ALB"),
                TargetType="instance",
                HealthCheckProtocol="HTTP",
                HealthCheckPath="/",
            )["TargetGroups"][0]
            self.ctx.target_group_arn = target_group["TargetGroupArn"]
            resources.append(
                ProvisionedResource(step_name, "target_group", self.ctx.target_group_arn)
            )

        if self.ctx.instance_ids:
            self.elbv2.register_targets(
                TargetGroupArn=self.ctx.target_group_arn,
                Targets=[{"Id": instance_id, "Port": 80} for instance_id in self.ctx.instance_ids],
            )

        if self.ctx.load_balancer_arn is None:
            lb = self.elbv2.create_load_balancer(
                Name=self._short_name("alb"),
                Subnets=self.ctx.public_subnet_ids,
                SecurityGroups=[self.ctx.security_group_ids["web"]],
                Scheme="internet-facing",
                Type="application",
                IpAddressType="ipv4",
                Tags=self._tags("load-balancer"),
            )["LoadBalancers"][0]
            self.ctx.load_balancer_arn = lb["LoadBalancerArn"]
            self.ctx.load_balancer_dns = lb["DNSName"]
            resources.append(
                ProvisionedResource(step_name, "load_balancer", self.ctx.load_balancer_arn)
            )

        listener = self.elbv2.create_listener(
            LoadBalancerArn=self.ctx.load_balancer_arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": self.ctx.target_group_arn}],
        )["Listeners"][0]
        resources.append(ProvisionedResource(step_name, "listener", listener["ListenerArn"]))
        return resources

    def ensure_s3(self) -> list[ProvisionedResource]:
        services = self._services("S3")
        resources: list[ProvisionedResource] = []
        for index, service in enumerate(services, start=1):
            bucket = self._bucket_name(service.name, index)
            if bucket in self.ctx.bucket_names:
                continue
            args: dict[str, Any] = {"Bucket": bucket}
            if self.ctx.region != "us-east-1":
                args["CreateBucketConfiguration"] = {"LocationConstraint": self.ctx.region}
            self.s3.create_bucket(**args)
            self.s3.put_public_access_block(
                Bucket=bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            self.s3.put_bucket_encryption(
                Bucket=bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            )
            if service.config.get("versioning"):
                self.s3.put_bucket_versioning(
                    Bucket=bucket,
                    VersioningConfiguration={"Status": "Enabled"},
                )
            self.ctx.bucket_names.append(bucket)
            resources.append(ProvisionedResource("S3", "s3_bucket", bucket))
        return resources

    def ensure_cloudfront(self) -> list[ProvisionedResource]:
        if not self._services("CloudFront"):
            return []
        self.ensure_s3()
        if not self.ctx.bucket_names:
            return []

        bucket = self.ctx.bucket_names[0]
        origin_id = f"s3-{bucket}"
        distribution = self.cloudfront.create_distribution(
            DistributionConfig={
                "CallerReference": self._name("cf"),
                "Comment": f"MomOps {self.ctx.app_id} CDN",
                "Enabled": True,
                "Origins": {
                    "Quantity": 1,
                    "Items": [
                        {
                            "Id": origin_id,
                            "DomainName": f"{bucket}.s3.{self.ctx.region}.amazonaws.com",
                            "S3OriginConfig": {"OriginAccessIdentity": ""},
                        }
                    ],
                },
                "DefaultCacheBehavior": {
                    "TargetOriginId": origin_id,
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "AllowedMethods": {
                        "Quantity": 2,
                        "Items": ["GET", "HEAD"],
                        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                    },
                    "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}},
                    "MinTTL": 0,
                },
            }
        )["Distribution"]
        return [ProvisionedResource("CloudFront", "cloudfront_distribution", distribution["Id"])]

    def ensure_sqs(self, step_name: str) -> list[ProvisionedResource]:
        queue = self.sqs.create_queue(
            QueueName=self._name("queue"),
            Attributes={"SqsManagedSseEnabled": "true", "VisibilityTimeout": "30"},
            tags={tag["Key"]: tag["Value"] for tag in self._tags("queue")},
        )
        return [ProvisionedResource(step_name, "sqs_queue", queue["QueueUrl"])]

    def ensure_ecs_cluster(self, step_name: str) -> list[ProvisionedResource]:
        cluster = self.ecs.create_cluster(
            clusterName=self._name("cluster"),
            tags=[{"key": tag["Key"], "value": tag["Value"]} for tag in self._tags("cluster")],
        )["cluster"]
        return [ProvisionedResource(step_name, "ecs_cluster", cluster["clusterArn"])]

    def ensure_secret(self, step_name: str) -> list[ProvisionedResource]:
        secret = self.secretsmanager.create_secret(
            Name=self._name("secret"),
            Description="MomOps generated deployment secret placeholder",
            SecretString='{"status":"created-by-momops"}',
            Tags=self._tags("secret"),
        )
        return [ProvisionedResource(step_name, "secret", secret["ARN"])]

    def ensure_redis(self, step_name: str) -> list[ProvisionedResource]:
        redis_service = self._first_service("ElastiCache")
        if redis_service is None:
            return []

        self.ensure_vpc()
        self.ensure_security_groups()
        subnet_group = self._name("redis-subnets")
        cluster_id = self._name("redis")
        self.elasticache.create_cache_subnet_group(
            CacheSubnetGroupName=subnet_group,
            CacheSubnetGroupDescription="MomOps Redis subnet group",
            SubnetIds=self.ctx.subnet_ids,
        )
        self.elasticache.create_cache_cluster(
            CacheClusterId=cluster_id,
            Engine="redis",
            CacheNodeType=redis_service.instance_type or "cache.t3.micro",
            NumCacheNodes=1,
            CacheSubnetGroupName=subnet_group,
            SecurityGroupIds=[self.ctx.security_group_ids["app"]],
            Tags=self._tags("redis"),
        )
        return [
            ProvisionedResource(step_name, "elasticache_subnet_group", subnet_group),
            ProvisionedResource(step_name, "elasticache_cluster", cluster_id),
        ]

    def ensure_api_gateway(self, step_name: str) -> list[ProvisionedResource]:
        api = self.apigatewayv2.create_api(
            Name=self._name("http-api"),
            ProtocolType="HTTP",
            Tags={tag["Key"]: tag["Value"] for tag in self._tags("api-gateway")},
        )
        return [ProvisionedResource(step_name, "api_gateway", api["ApiId"])]

    def ensure_monitoring(self, step_name: str) -> list[ProvisionedResource]:
        dashboard_name = self._name("dashboard")
        self.cloudwatch.put_dashboard(DashboardName=dashboard_name, DashboardBody='{"widgets":[]}')
        return [ProvisionedResource(step_name, "cloudwatch_dashboard", dashboard_name)]

    def ensure_certificate(self, step_name: str) -> list[ProvisionedResource]:
        domain_name = self.blueprint.requirement.extra_hints.get("domain_name")
        if not domain_name:
            logger.info("No domain_name hint provided; leaving ALB on HTTP endpoint")
            return []
        acm = self.provider.client("acm")
        cert = acm.request_certificate(
            DomainName=str(domain_name),
            ValidationMethod="DNS",
            Tags=self._tags("certificate"),
        )
        return [ProvisionedResource(step_name, "certificate", cert["CertificateArn"])]

    def ensure_route53_ready(self, step_name: str) -> list[ProvisionedResource]:
        domain_name = self.blueprint.requirement.extra_hints.get("domain_name")
        if not domain_name:
            logger.info("No domain_name hint provided; Route53 record creation skipped")
            return []
        route53 = self.provider.client("route53")
        zones = route53.list_hosted_zones_by_name(DNSName=str(domain_name), MaxItems="1")
        zone_id = zones.get("HostedZones", [{}])[0].get("Id")
        if not zone_id:
            raise DeploymentError(f"No Route53 hosted zone found for {domain_name}")
        return [ProvisionedResource(step_name, "route53_zone", str(zone_id))]

    def _latest_amazon_linux_ami(self) -> str:
        parameter = self.ssm.get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
        )
        return str(parameter["Parameter"]["Value"])

    def _service_config(self, service_name: str) -> dict[str, Any]:
        service = self._first_service(service_name)
        return service.config if service else {}

    def _first_service(self, service_name: str) -> AWSService | None:
        services = self._services(service_name)
        return services[0] if services else None

    def _services(self, service_name: str) -> list[AWSService]:
        wanted = service_name.lower()
        return [
            service for service in self.blueprint.aws_services if service.service.lower() == wanted
        ]

    def _tags(self, name: str) -> list[dict[str, str]]:
        return AWSProvider.tags(self.ctx.app_id, self._name(name))

    def _name(self, suffix: str) -> str:
        safe_suffix = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in suffix.lower())
        return f"momops-{self.ctx.app_id}-{safe_suffix}"[:63].rstrip("-")

    def _short_name(self, suffix: str) -> str:
        return self._name(suffix)[:32].rstrip("-")

    def _bucket_name(self, name: str, index: int) -> str:
        safe = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
        return f"momops-{self.ctx.app_id}-{index}-{safe}"[:63].rstrip("-")

    @staticmethod
    def _password() -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(24))

    @staticmethod
    def _require(value: str | None, message: str) -> str:
        if value is None:
            raise DeploymentError(message)
        return value


class Deployer:
    """
    Orchestrates the full AWS provisioning sequence for a blueprint.

    Usage:
        deployer = Deployer(blueprint, dry_run=False)
        async for event in deployer.deploy():
            print(event.message)
        app = deployer.result
    """

    def __init__(self, blueprint: ArchitectureBlueprint, dry_run: bool = False) -> None:
        self.blueprint = blueprint
        self.dry_run = dry_run
        self._provisioned: dict[str, str] = {}
        self._resources: list[ProvisionedResource] = []
        self._start_time = 0.0
        self._result: DeployedApp | None = None
        self._provisioner: AWSProvisioner | None = None

    @property
    def result(self) -> DeployedApp:
        if self._result is None:
            raise RuntimeError("deploy() has not completed successfully yet")
        return self._result

    async def deploy(self) -> AsyncGenerator[DeployEvent, None]:
        """
        Execute the full deployment sequence.
        Yields DeployEvent for each step.
        On failure: initiates rollback, then raises DeploymentError.
        """
        validate_blueprint(self.blueprint)

        self._start_time = time.monotonic()
        app_id = str(uuid.uuid4())[:8]

        if self.dry_run:
            async for event in self._dry_run_sequence():
                yield event
            self._result = self._build_result(app_id, endpoint="https://dry-run.momops.dev")
            return

        try:
            async for event in self._provision_sequence(app_id):
                yield event
        except (BotoCoreError, ClientError, Exception) as exc:
            logger.exception("Deployment failed: %s; initiating rollback", exc)
            yield DeployEvent(
                step="rollback",
                status=DeployStatus.IN_PROGRESS,
                message="Something went wrong; rolling back created resources...",
                elapsed_seconds=self._elapsed(),
            )
            await self._rollback()
            yield DeployEvent(
                step="rollback",
                status=DeployStatus.ROLLED_BACK,
                message="Rollback attempt complete.",
                elapsed_seconds=self._elapsed(),
            )
            raise DeploymentError(str(exc)) from exc

    async def _provision_sequence(self, app_id: str) -> AsyncGenerator[DeployEvent, None]:
        """Execute each deploy step in order with real AWS calls."""
        if boto3 is None:
            raise DeploymentError(
                "boto3 is required for real AWS deployments; use dry_run=True locally"
            )

        self._provisioner = AWSProvisioner(self.blueprint, app_id)

        for step in sorted(self.blueprint.deploy_steps, key=lambda s: s.order):
            yield DeployEvent(
                step=step.name,
                status=DeployStatus.IN_PROGRESS,
                message=f"Starting {step.description}...",
                elapsed_seconds=self._elapsed(),
            )

            resources = await asyncio.to_thread(self._provisioner.provision, step.name)
            self._resources.extend(resources)
            resource_ids = [resource.resource_id for resource in resources]
            if resource_ids:
                self._provisioned[step.name] = ",".join(resource_ids)

            yield DeployEvent(
                step=step.name,
                status=DeployStatus.COMPLETE,
                message=f"Completed {step.description}",
                aws_resource_id=self._provisioned.get(step.name),
                elapsed_seconds=self._elapsed(),
            )

        endpoint = self._provisioner.endpoint()
        self._result = self._build_result(app_id, endpoint=endpoint)

        yield DeployEvent(
            step="complete",
            status=DeployStatus.COMPLETE,
            message=f"Deployment complete: {endpoint or 'AWS resources created'}",
            elapsed_seconds=self._elapsed(),
        )

    async def _dry_run_sequence(self) -> AsyncGenerator[DeployEvent, None]:
        """Validate all steps without touching real AWS."""
        for step in sorted(self.blueprint.deploy_steps, key=lambda s: s.order):
            await asyncio.sleep(0.05)
            yield DeployEvent(
                step=step.name,
                status=DeployStatus.COMPLETE,
                message=f"[dry-run] Completed {step.description}",
                elapsed_seconds=self._elapsed(),
            )

        yield DeployEvent(
            step="dry_run_complete",
            status=DeployStatus.COMPLETE,
            message="[dry-run] All checks passed; ready to deploy for real.",
            elapsed_seconds=self._elapsed(),
        )

    async def _rollback(self) -> None:
        """Rollback by deleting AWS resources in reverse order."""
        if not self._resources:
            logger.info("No resources to rollback")
            return

        logger.info("Rolling back %d resources", len(self._resources))
        provisioner = self._provisioner
        if provisioner is None:
            logger.warning("No provisioner available for rollback; skipping")
            self._provisioned.clear()
            self._resources.clear()
            return

        # Iterate in reverse order
        for resource in reversed(self._resources):
            try:
                await asyncio.to_thread(provisioner.rollback_resource, resource)
                logger.info(
                    "Deleted resource: %s %s",
                    resource.resource_type,
                    resource.resource_id,
                )
            except Exception as e:
                logger.error(
                    "Failed to delete resource %s %s: %s",
                    resource.resource_type,
                    resource.resource_id,
                    e,
                )
        self._provisioned.clear()
        self._resources.clear()

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._start_time, 1)

    def _build_result(self, app_id: str, endpoint: str | None) -> DeployedApp:
        return DeployedApp(
            app_id=app_id,
            name=self.blueprint.recipe_name,
            endpoint=endpoint,
            region=self.blueprint.requirement.region,
            blueprint=self.blueprint,
            aws_resource_ids=self._provisioned.copy(),
            deployed_at=datetime.now(UTC).isoformat(),
        )


async def deploy_blueprint(
    blueprint: ArchitectureBlueprint,
    dry_run: bool = False,
    on_event: None = None,
) -> DeployedApp:
    """
    Convenience wrapper: deploy a blueprint and return the final DeployedApp.
    Prints progress to logs unless events are handled from Deployer.deploy().
    """
    deployer = Deployer(blueprint, dry_run=dry_run)
    async for event in deployer.deploy():
        logger.info("[%s] %s (%.1fs)", event.status, event.message, event.elapsed_seconds)
    return deployer.result

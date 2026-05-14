"""
AGENT-2: Recipe Engine
Maps an InfraRequirement to a battle-tested ArchitectureBlueprint.
Each recipe is a pre-validated cloud pattern (API+DB, blog, ML endpoint, etc.)
"""

from __future__ import annotations

import logging

from momops.models import (
    AWSService,
    ArchitectureBlueprint,
    CostBreakdown,
    DatabaseType,
    DeployStep,
    InfraRequirement,
    LineItem,
    ScaleHint,
    SecurityManifest,
    ServiceType,
)

logger = logging.getLogger(__name__)

# ── Security manifest (applied to ALL recipes) ─────────────────────────────

_DEFAULT_SECURITY = SecurityManifest(
    ssl_enabled=True,
    backup_enabled=True,
    vpc_isolated=True,
    iam_least_privilege=True,
    monitoring_enabled=True,
    ddos_protection=True,
    encryption_at_rest=True,
    encryption_in_transit=True,
    applied_rules=[
        "SSL/TLS with ACM auto-renewal",
        "Daily RDS snapshots (7-day retention)",
        "VPC with private subnets for DB tier",
        "IAM roles — no root credentials",
        "CloudWatch alarms: CPU, memory, 5xx errors",
        "AWS Shield Standard",
        "EBS encryption + RDS encryption at rest",
    ],
)

# ── Instance type maps by scale ─────────────────────────────────────────────

_EC2_BY_SCALE: dict[ScaleHint, str] = {
    ScaleHint.HOBBY: "t3.micro",
    ScaleHint.STARTUP: "t3.medium",
    ScaleHint.SCALE: "c6i.xlarge",
    ScaleHint.ENTERPRISE: "c6i.4xlarge",
}

_RDS_BY_SCALE: dict[ScaleHint, str] = {
    ScaleHint.HOBBY: "db.t3.micro",
    ScaleHint.STARTUP: "db.t3.medium",
    ScaleHint.SCALE: "db.r6g.large",
    ScaleHint.ENTERPRISE: "db.r6g.4xlarge",
}

# ── Monthly cost tables (USD) — approximate, updated Q1 2025 ───────────────

_COMPUTE_COST: dict[str, float] = {
    "t3.micro": 8.0,
    "t3.medium": 30.0,
    "c6i.xlarge": 122.0,
    "c6i.4xlarge": 490.0,
}

_RDS_COST: dict[str, float] = {
    "db.t3.micro": 14.0,
    "db.t3.medium": 52.0,
    "db.r6g.large": 175.0,
    "db.r6g.4xlarge": 700.0,
}

_NETWORKING_COST: dict[ScaleHint, float] = {
    ScaleHint.HOBBY: 2.0,
    ScaleHint.STARTUP: 10.0,
    ScaleHint.SCALE: 35.0,
    ScaleHint.ENTERPRISE: 120.0,
}


def _build_cost(
    req: InfraRequirement,
    ec2_type: str,
    db_type: str | None,
    include_cdn: bool = False,
    include_redis: bool = False,
) -> CostBreakdown:
    items: list[LineItem] = [
        LineItem(
            label="compute",
            service="EC2",
            instance_type=ec2_type,
            monthly_usd=_COMPUTE_COST.get(ec2_type, 30.0),
        ),
        LineItem(
            label="networking",
            service="ALB + Data Transfer",
            monthly_usd=_NETWORKING_COST[req.scale],
        ),
    ]

    if db_type:
        items.append(LineItem(
            label="database",
            service="RDS",
            instance_type=db_type,
            monthly_usd=_RDS_COST.get(db_type, 50.0),
        ))

    if include_cdn:
        items.append(LineItem(label="cdn", service="CloudFront", monthly_usd=5.0))

    if include_redis:
        items.append(LineItem(
            label="cache",
            service="ElastiCache Redis",
            instance_type="cache.t3.micro",
            monthly_usd=16.0,
        ))

    items.append(LineItem(label="monitoring", service="CloudWatch", monthly_usd=3.0))

    # savings hint: spot instances could save ~40% on compute
    compute_cost = _COMPUTE_COST.get(ec2_type, 30.0)
    savings = round(compute_cost * 0.40, 2)

    return CostBreakdown(items=items, savings_available=savings)


# ── Recipe builders ─────────────────────────────────────────────────────────


def _api_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    ec2 = _EC2_BY_SCALE[req.scale]
    db = _RDS_BY_SCALE[req.scale] if req.database != DatabaseType.NONE else None
    db_label = req.database.value if req.database != DatabaseType.NONE else None

    services: list[AWSService] = [
        AWSService(name="VPC", service="VPC", config={"cidr": "10.0.0.0/16", "az_count": 2}),
        AWSService(name="Application Load Balancer", service="ALB"),
        AWSService(
            name="API Servers",
            service="EC2",
            instance_type=ec2,
            config={"min": 1, "max": 4 if req.scale != ScaleHint.HOBBY else 1},
        ),
    ]

    if db:
        services.append(AWSService(
            name=f"{db_label or 'Postgres'} Database",
            service="RDS",
            instance_type=db,
            config={"engine": db_label or "postgres", "multi_az": req.scale != ScaleHint.HOBBY},
        ))

    if req.auth_required:
        services.append(AWSService(name="Secrets Manager", service="SecretsManager"))

    steps: list[DeployStep] = [
        DeployStep(order=1, name="VPC", description="Create VPC + subnets", aws_service="EC2", estimated_duration_seconds=15),
        DeployStep(order=2, name="Security Groups", description="Configure firewall rules", aws_service="EC2", estimated_duration_seconds=10),
        DeployStep(order=3, name="Database", description=f"Provision RDS {db_label or 'postgres'}", aws_service="RDS", estimated_duration_seconds=180, rollback_action="delete_db_instance"),
        DeployStep(order=4, name="Compute", description=f"Launch EC2 {ec2} + autoscaling", aws_service="EC2", estimated_duration_seconds=60, rollback_action="terminate_instances"),
        DeployStep(order=5, name="Load Balancer", description="Create ALB + target group", aws_service="ALB", estimated_duration_seconds=30, rollback_action="delete_load_balancer"),
        DeployStep(order=6, name="SSL Certificate", description="Issue ACM cert + attach to ALB", aws_service="ACM", estimated_duration_seconds=20),
        DeployStep(order=7, name="Monitoring", description="CloudWatch alarms + dashboards", aws_service="CloudWatch", estimated_duration_seconds=15),
    ]

    return ArchitectureBlueprint(
        recipe_id="api-v1",
        recipe_name="Production API",
        description=f"Load-balanced {ec2} API tier" + (f" + RDS {db_label}" if db else ""),
        aws_services=services,
        deploy_steps=steps,
        cost=_build_cost(req, ec2, db, include_cdn=req.cdn_required),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _blog_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    ec2 = _EC2_BY_SCALE[req.scale]
    db = _RDS_BY_SCALE[req.scale]

    services = [
        AWSService(name="VPC", service="VPC", config={"cidr": "10.0.0.0/16"}),
        AWSService(name="ALB", service="ALB"),
        AWSService(name="Web Servers", service="EC2", instance_type=ec2),
        AWSService(name="Postgres DB", service="RDS", instance_type=db, config={"engine": "postgres"}),
        AWSService(name="S3 Media Bucket", service="S3", config={"versioning": True}),
        AWSService(name="CloudFront CDN", service="CloudFront", config={"price_class": "PriceClass_100"}),
    ]

    steps = [
        DeployStep(order=1, name="VPC", description="Network foundation", aws_service="EC2", estimated_duration_seconds=15),
        DeployStep(order=2, name="Database", description="Postgres for content", aws_service="RDS", estimated_duration_seconds=180, rollback_action="delete_db_instance"),
        DeployStep(order=3, name="S3 + CDN", description="Media storage + CloudFront", aws_service="S3", estimated_duration_seconds=30),
        DeployStep(order=4, name="Compute", description=f"EC2 {ec2}", aws_service="EC2", estimated_duration_seconds=60, rollback_action="terminate_instances"),
        DeployStep(order=5, name="Load Balancer", description="ALB + HTTPS", aws_service="ALB", estimated_duration_seconds=30),
        DeployStep(order=6, name="Monitoring", description="CloudWatch", aws_service="CloudWatch", estimated_duration_seconds=15),
    ]

    return ArchitectureBlueprint(
        recipe_id="blog-v1",
        recipe_name="Content Site / Blog",
        description=f"WordPress-ready blog — {ec2} + Postgres + CloudFront",
        aws_services=services,
        deploy_steps=steps,
        cost=_build_cost(req, ec2, db, include_cdn=True),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _ml_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    # ML gets GPU or compute-optimized instances
    instance = "g4dn.xlarge" if req.scale == ScaleHint.SCALE else "c6i.2xlarge"
    monthly = 400.0 if instance == "g4dn.xlarge" else 240.0

    services = [
        AWSService(name="VPC", service="VPC"),
        AWSService(name="Inference Server", service="EC2", instance_type=instance),
        AWSService(name="Model Storage", service="S3"),
        AWSService(name="API Gateway", service="APIGateway"),
        AWSService(name="Lambda", service="Lambda", config={"runtime": "python3.12"}),
    ]

    steps = [
        DeployStep(order=1, name="VPC + S3", description="Network + model storage", aws_service="EC2", estimated_duration_seconds=20),
        DeployStep(order=2, name="Compute", description=f"Launch {instance}", aws_service="EC2", estimated_duration_seconds=90, rollback_action="terminate_instances"),
        DeployStep(order=3, name="API Gateway", description="HTTP API + Lambda proxy", aws_service="APIGateway", estimated_duration_seconds=30),
        DeployStep(order=4, name="Monitoring", description="Inference latency alarms", aws_service="CloudWatch", estimated_duration_seconds=15),
    ]

    items = [
        LineItem(label="compute", service="EC2", instance_type=instance, monthly_usd=monthly),
        LineItem(label="model_storage", service="S3", monthly_usd=5.0),
        LineItem(label="api_gateway", service="API Gateway", monthly_usd=8.0),
        LineItem(label="networking", service="Data Transfer", monthly_usd=10.0),
        LineItem(label="monitoring", service="CloudWatch", monthly_usd=3.0),
    ]

    return ArchitectureBlueprint(
        recipe_id="ml-v1",
        recipe_name="ML Inference Endpoint",
        description=f"GPU-accelerated ML serving on {instance} + API Gateway",
        aws_services=services,
        deploy_steps=steps,
        cost=CostBreakdown(items=items, savings_available=round(monthly * 0.60, 2)),  # spot saves 60% on GPU
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _realtime_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    ec2 = _EC2_BY_SCALE[req.scale]

    services = [
        AWSService(name="VPC", service="VPC"),
        AWSService(name="WebSocket Servers", service="EC2", instance_type=ec2, config={"min": 2, "max": 8}),
        AWSService(name="ElastiCache Redis", service="ElastiCache", instance_type="cache.r6g.large"),
        AWSService(name="ALB (WebSocket)", service="ALB", config={"protocol": "wss"}),
        AWSService(name="SQS Queue", service="SQS"),
    ]

    steps = [
        DeployStep(order=1, name="VPC", description="Network + subnets", aws_service="EC2", estimated_duration_seconds=15),
        DeployStep(order=2, name="Redis", description="ElastiCache for pub/sub", aws_service="ElastiCache", estimated_duration_seconds=120),
        DeployStep(order=3, name="Compute", description=f"WebSocket servers {ec2}", aws_service="EC2", estimated_duration_seconds=60, rollback_action="terminate_instances"),
        DeployStep(order=4, name="ALB + wss://", description="WebSocket-capable load balancer", aws_service="ALB", estimated_duration_seconds=30),
        DeployStep(order=5, name="SQS", description="Message queue for reliability", aws_service="SQS", estimated_duration_seconds=10),
        DeployStep(order=6, name="Monitoring", description="Connection count + latency alarms", aws_service="CloudWatch", estimated_duration_seconds=15),
    ]

    return ArchitectureBlueprint(
        recipe_id="realtime-v1",
        recipe_name="Real-time WebSocket Server",
        description=f"Horizontally scalable WebSocket tier on {ec2} + Redis pub/sub",
        aws_services=services,
        deploy_steps=steps,
        cost=_build_cost(req, ec2, None, include_redis=True),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _static_site_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    services = [
        AWSService(name="Static Asset Bucket", service="S3", config={"versioning": True}),
        AWSService(name="CloudFront CDN", service="CloudFront", config={"price_class": "PriceClass_100"}),
        AWSService(name="DNS", service="Route53"),
    ]
    steps = [
        DeployStep(order=1, name="S3", description="Create encrypted static site bucket", aws_service="S3", estimated_duration_seconds=15),
        DeployStep(order=2, name="CloudFront", description="Create CDN distribution with HTTPS", aws_service="CloudFront", estimated_duration_seconds=60),
        DeployStep(order=3, name="Route53", description="Prepare DNS records", aws_service="Route53", estimated_duration_seconds=10),
        DeployStep(order=4, name="Monitoring", description="CloudWatch request and error alarms", aws_service="CloudWatch", estimated_duration_seconds=10),
    ]
    items = [
        LineItem(label="storage", service="S3", monthly_usd=5.0),
        LineItem(label="cdn", service="CloudFront", monthly_usd=5.0),
        LineItem(label="dns", service="Route53", monthly_usd=1.0),
        LineItem(label="monitoring", service="CloudWatch", monthly_usd=3.0),
    ]
    return ArchitectureBlueprint(
        recipe_id="static-site-v1",
        recipe_name="Static Site",
        description="S3 static hosting with CloudFront, HTTPS, and DNS",
        aws_services=services,
        deploy_steps=steps,
        cost=CostBreakdown(items=items, savings_available=0.0),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _database_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    db = _RDS_BY_SCALE[req.scale]
    engine = req.database.value if req.database != DatabaseType.NONE else "postgres"
    services = [
        AWSService(name="VPC", service="VPC", config={"cidr": "10.0.0.0/16", "private_subnets": True}),
        AWSService(name=f"{engine.title()} Database", service="RDS", instance_type=db, config={"engine": engine, "multi_az": req.scale != ScaleHint.HOBBY}),
        AWSService(name="Secrets", service="SecretsManager"),
    ]
    steps = [
        DeployStep(order=1, name="VPC", description="Create private database network", aws_service="EC2", estimated_duration_seconds=15),
        DeployStep(order=2, name="Security Groups", description="Restrict database ingress", aws_service="EC2", estimated_duration_seconds=10),
        DeployStep(order=3, name="Database", description=f"Provision encrypted RDS {engine}", aws_service="RDS", estimated_duration_seconds=180, rollback_action="delete_db_instance"),
        DeployStep(order=4, name="Secrets", description="Store generated credentials", aws_service="SecretsManager", estimated_duration_seconds=10),
        DeployStep(order=5, name="Monitoring", description="RDS CPU, storage, and connection alarms", aws_service="CloudWatch", estimated_duration_seconds=15),
    ]
    return ArchitectureBlueprint(
        recipe_id="database-v1",
        recipe_name="Managed Database",
        description=f"Private encrypted RDS {engine} on {db}",
        aws_services=services,
        deploy_steps=steps,
        cost=_build_cost(req, "t3.micro", db),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


def _ecommerce_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    base = _api_recipe(req.model_copy(update={"database": DatabaseType.POSTGRES, "cdn_required": True}))
    services = [
        *base.aws_services,
        AWSService(name="Product Media", service="S3", config={"versioning": True}),
        AWSService(name="Order Queue", service="SQS"),
        AWSService(name="Transactional Secrets", service="SecretsManager"),
    ]
    items = [
        *base.cost.items,
        LineItem(label="queue", service="SQS", monthly_usd=1.0),
        LineItem(label="media_storage", service="S3", monthly_usd=5.0),
    ]
    return base.model_copy(
        update={
            "recipe_id": "ecommerce-v1",
            "recipe_name": "Ecommerce Platform",
            "description": "Autoscaled storefront API with Postgres, CDN, media storage, and order queue",
            "aws_services": services,
            "cost": CostBreakdown(items=items, savings_available=base.cost.savings_available),
        }
    )


def _microservices_recipe(req: InfraRequirement) -> ArchitectureBlueprint:
    ec2 = _EC2_BY_SCALE[req.scale]
    db = _RDS_BY_SCALE[req.scale]
    services = [
        AWSService(name="VPC", service="VPC", config={"cidr": "10.0.0.0/16", "az_count": 2}),
        AWSService(name="ECS Cluster", service="ECS", config={"launch_type": "EC2"}),
        AWSService(name="Service Nodes", service="EC2", instance_type=ec2, config={"min": 2, "max": 10}),
        AWSService(name="Internal ALB", service="ALB", config={"scheme": "internal"}),
        AWSService(name="Public ALB", service="ALB", config={"scheme": "internet-facing"}),
        AWSService(name="Shared Postgres", service="RDS", instance_type=db, config={"engine": "postgres"}),
        AWSService(name="Service Queue", service="SQS"),
    ]
    steps = [
        DeployStep(order=1, name="VPC", description="Create multi-AZ network", aws_service="EC2", estimated_duration_seconds=15),
        DeployStep(order=2, name="ECS Cluster", description="Create cluster and capacity provider", aws_service="ECS", estimated_duration_seconds=45),
        DeployStep(order=3, name="Database", description="Provision shared Postgres", aws_service="RDS", estimated_duration_seconds=180, rollback_action="delete_db_instance"),
        DeployStep(order=4, name="Load Balancers", description="Create public and internal ALBs", aws_service="ALB", estimated_duration_seconds=45),
        DeployStep(order=5, name="Services", description=f"Launch service nodes on {ec2}", aws_service="EC2", estimated_duration_seconds=75, rollback_action="terminate_instances"),
        DeployStep(order=6, name="Queue", description="Create async service queue", aws_service="SQS", estimated_duration_seconds=10),
        DeployStep(order=7, name="Monitoring", description="Service-level alarms and dashboards", aws_service="CloudWatch", estimated_duration_seconds=20),
    ]
    return ArchitectureBlueprint(
        recipe_id="microservices-v1",
        recipe_name="Microservices Cluster",
        description=f"ECS-backed services on {ec2} with shared RDS and internal routing",
        aws_services=services,
        deploy_steps=steps,
        cost=_build_cost(req, ec2, db, include_redis=req.websocket_required),
        security=_DEFAULT_SECURITY,
        requirement=req,
    )


# ── Public interface ────────────────────────────────────────────────────────

_RECIPE_MAP = {
    ServiceType.API: _api_recipe,
    ServiceType.BLOG: _blog_recipe,
    ServiceType.ML: _ml_recipe,
    ServiceType.REALTIME: _realtime_recipe,
    ServiceType.ECOMMERCE: _ecommerce_recipe,
    ServiceType.MICROSERVICES: _microservices_recipe,
    ServiceType.DATABASE: _database_recipe,
    ServiceType.STORAGE: _static_site_recipe,
    ServiceType.UNKNOWN: _api_recipe,
}


def get_blueprint(req: InfraRequirement) -> ArchitectureBlueprint:
    """
    Select and build the best-fit architecture blueprint for a requirement.

    Args:
        req: Parsed InfraRequirement from the intent parser

    Returns:
        ArchitectureBlueprint with full cost breakdown, services, and deploy steps
    """
    builder = _RECIPE_MAP.get(req.service_type, _api_recipe)
    blueprint = builder(req)
    logger.info(
        "Recipe selected: %s | Cost: $%.2f/mo",
        blueprint.recipe_id,
        blueprint.cost.total_monthly,
    )
    return blueprint

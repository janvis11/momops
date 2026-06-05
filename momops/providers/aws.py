"""AWS client factory and small provider helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import boto3
    from botocore.config import Config
else:
    try:
        import boto3
        from botocore.config import Config
    except ModuleNotFoundError:  # pragma: no cover
        boto3: Any = None
        Config: Any = None

from momops.config import get_settings


class AWSProvider:
    """Thin wrapper around boto3 session/client creation."""

    def __init__(self, region: str | None = None) -> None:
        settings = get_settings()
        self.region = region or settings.aws_default_region
        if boto3 is None:
            raise RuntimeError("boto3 is required for AWS provider operations")
        self.session = boto3.session.Session(region_name=self.region)

    def client(self, service_name: str) -> Any:
        """Create a boto3 client with conservative retry defaults."""
        if Config is None:
            return self.session.client(service_name)
        return self.session.client(
            service_name,
            config=Config(retries={"max_attempts": 10, "mode": "standard"}),
        )

    def resource(self, service_name: str) -> Any:
        """Create a boto3 resource with conservative retry defaults."""
        if Config is None:
            return self.session.resource(service_name)
        return self.session.resource(
            service_name,
            config=Config(retries={"max_attempts": 10, "mode": "standard"}),
        )

    @staticmethod
    def tags(
        app_id: str, name: str, extra: Mapping[str, str] | None = None
    ) -> list[dict[str, str]]:
        """Build common AWS tag structures."""
        tag_map = {
            "ManagedBy": "MomOps",
            "MomOpsAppId": app_id,
            "Name": name,
        }
        if extra:
            tag_map.update(extra)
        return [{"Key": key, "Value": value} for key, value in tag_map.items()]

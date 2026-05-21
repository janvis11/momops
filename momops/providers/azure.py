"""Azure provider placeholder."""

from __future__ import annotations


class AzureProvider:
    """Future Azure support target."""

    available = False
    planned_release = "Q2 2026"

    def client(self, service_name: str) -> None:
        raise NotImplementedError(
            f"Azure support is planned for {self.planned_release}; requested {service_name}."
        )

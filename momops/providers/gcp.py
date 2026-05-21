"""Google Cloud provider placeholder."""

from __future__ import annotations


class GCPProvider:
    """Future GCP support target."""

    available = False
    planned_release = "Q3 2026"

    def client(self, service_name: str) -> None:
        raise NotImplementedError(
            f"GCP support is planned for {self.planned_release}; requested {service_name}."
        )

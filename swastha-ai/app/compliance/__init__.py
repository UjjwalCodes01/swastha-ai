"""Layer 4 compliance and governance modules."""

__all__ = ["CompliancePipeline"]


def __getattr__(name: str):
    if name == "CompliancePipeline":
        from app.compliance.pipeline import CompliancePipeline
        return CompliancePipeline
    raise AttributeError(name)

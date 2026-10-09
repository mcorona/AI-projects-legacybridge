"""Deployment plumbing for AWS (ADR-014): the job store, the asynchronous front, the worker and the
secrets. No model logic lives here; boto3 may be imported in this package and in ``llm/`` only."""

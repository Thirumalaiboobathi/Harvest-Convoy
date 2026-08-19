"""Shared plumbing for the three read-only reporting scripts (ADR-010
Parts 1-3): a provenance header every output carries, and (in config.py)
the one named policy parameter Part 2 needs. Nothing in this package
writes to Storage or calls Bedrock.
"""

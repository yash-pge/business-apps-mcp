# Business Apps Search MCP server (Python/FastMCP).
# This setup.py exists so the Publish-Wizscan buildspec can extract a version tag
# (same as ecm-mcp-server). Keep in sync with pyproject.toml -> version.
from setuptools import setup

setup(
    name='business-apps-search',
    # Four-part release number, not an IP address. Keep the literal and its single
    # quotes verbatim on this line — the Wizscan buildspec greps it out to tag the
    # image, and a second quoted match anywhere in this file corrupts that value.
    version='1.0.0.0'  # NOSONAR - python:S1313 false positive (version, not an IP)
)

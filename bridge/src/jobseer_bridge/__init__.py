"""Stdio tool server for JobSeer."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jobseer-bridge")
except PackageNotFoundError:
    __version__ = "0.0.0"

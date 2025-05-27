import pathlib
from setuptools import setup, find_namespace_packages


def read_requirements(filename: str) -> list[str]:
    """Read requirements from file, ignoring comments and -r directives."""
    path = pathlib.Path(filename)
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", "-r"))
    ]


# Read requirements files
common_requires = read_requirements("requirements-common.txt")
parsers_requires = read_requirements("requirements-parsers.txt")
api_requires = read_requirements("requirements-api.txt")
workers_requires = read_requirements("requirements-workers.txt")
dev_requires = read_requirements("requirements-dev.txt")

setup(
    name="memory",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_namespace_packages(where="src"),
    python_requires=">=3.10",
    extras_require={
        "api": api_requires + common_requires,
        "workers": workers_requires + common_requires + parsers_requires,
        "common": common_requires,
        "dev": dev_requires,
        "all": api_requires
        + workers_requires
        + common_requires
        + dev_requires
        + parsers_requires,
    },
)

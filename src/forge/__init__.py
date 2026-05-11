from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("forge")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0+local"

__all__ = ["__version__"]

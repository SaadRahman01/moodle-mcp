"""moodle-mcp — Model Context Protocol server for Moodle development."""
from .docs import DocHit, MoodleDocs, format_results, hits_to_dicts

__version__ = "0.2.0"
__all__ = ["MoodleDocs", "DocHit", "format_results", "hits_to_dicts", "__version__"]

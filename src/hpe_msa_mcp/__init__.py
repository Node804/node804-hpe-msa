"""Token-efficient, read-only MCP server for HPE MSA (Modular Smart Array) storage.

Designed around three principles:
  1. Lean responses by default — typical 40-70% token reduction vs unfiltered output.
  2. Default-deny RBAC — only read tools are exposed; MSA_MODE never registers writes.
  3. TLS verification on by default — MSA controller certs are often self-signed,
     so disabling verification requires an explicit opt-in.

See the README for installation, configuration, and the full tool catalog.
"""

from __future__ import annotations

__version__ = "0.1.0"

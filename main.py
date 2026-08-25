"""AgentCore Runtime deployment root entrypoint.

/var/task (the zip's extraction root) is the first entry on sys.path, so
`harvest_convoy` (copied in alongside this file) and all vendored
dependencies resolve as plain top-level imports -- see
docs/adr/ADR-006-deploy.md Decision 1.
"""

from harvest_convoy.app import app

if __name__ == "__main__":
    app.run()

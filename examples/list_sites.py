"""List sites on a server. Requires a prior `fcctl auth login` and FCCTL_KEY in the environment."""
import sys

from fcctl import Client
from fcctl.session_store import SessionStore

server = sys.argv[1] if len(sys.argv) > 1 else "server-xxxx"
c = Client(SessionStore(), team="TEAM-xxxxx")
try:
    for s in c.sites.list(server):
        print(s.get("name"), s.get("status"))
finally:
    c.close()

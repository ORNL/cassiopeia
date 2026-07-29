# MCP server authentication — options to consider

**Status:** undecided, deferred. Written 2026-07-29 alongside the `globus-auth`
branch, which secured the HTTP API but deliberately left `mcp_server.py` alone.

---

## Why this needs a decision

`mcp_server.py` does not go through the FastAPI app. Its lifespan calls
`launch_agents(scan_seconds, db_path)` and then talks to the Academy agent
handles and `PaperStore` **directly**:

```python
# mcp_server.py, _lifespan
db_path = os.environ.get("DB_PATH") or str(Path(_PROJECT_ROOT) / "cassiopeia.db")
async with launch_agents(scan_seconds, db_path) as (mining, rag, store):
    _mining_handle, _rag_handle, _paper_store = mining, rag, store
```

It opens the same database as the API server. So none of the tenancy work on
the `globus-auth` branch protects it:

- Five of its six tools take `researcher_id` as a caller-supplied parameter
  (`search_literature`, `get_top_papers`, `detect_contradictions`,
  `anchor_search`, `ask_knowledge_base`; only `litminer_status` does not) —
  the exact pattern removed from the HTTP API.
- There is no credential of any kind.
- Anything that can open a socket to the port can read or write any
  researcher's papers, proposals, profile and ratings.

What it *does* have is `TransportSecuritySettings` with DNS-rebinding
protection and localhost-only allowed hosts and origins, plus a `localhost`
default bind. **That is not authentication.** It stops a browser being tricked
into calling the server; it does not stop a caller that reaches the port.

Mitigating factor: the MCP server is not launched by `launch.sh`,
`docker-compose.yml`, `.gitlab-ci.yml` or the `.bat` scripts. It is dormant
today. The risk is someone deploying it later assuming the auth work covers it.

There is also a non-security problem with the current shape: running the API
and the MCP server together means **two agent runtimes, two ChromaDB clients
and two embedding models against one SQLite file**. WAL mode keeps that from
corrupting, but two independent background scanners on one database is a design
smell regardless of what is decided about auth.

---

## Option 1 — Document the boundary (do this regardless)

Add to the module docstring and README: the MCP server bypasses the API's
authentication, must stay bound to localhost, and is only safe in a
single-tenant context.

- **Effort:** minutes. **Risk:** none. **Solves:** nothing technically.
- Worth doing under every other option, because it is the only thing standing
  between "dormant" and "someone deploys it".

## Option 2 — Single-identity process

Bind the process to one researcher through configuration and drop
`researcher_id` from all five tool signatures.

```
CASSIOPEIA_MCP_RESEARCHER_ID=<globus identity uuid>
```

Better: a Globus **client-credentials** grant, so the service has a real Globus
identity of its own rather than an id copied from a config file.

- **Effort:** small — mirror the API fix, identity from config not from caller.
- **Fits:** how MCP is actually deployed today, where a server is launched by or
  for a single agent.
- **Limit:** one researcher per process. Multi-user means multi-process.
- **Still leaves:** the duplicate agent runtime and the second door to the DB.

## Option 3 — Make it a client of the HTTP API, not of the agents

Replace `launch_agents()` and direct `PaperStore` access with HTTP calls to the
API, forwarding the caller's bearer token.

- **Effort:** medium — rewrite each tool as an API call; the response shapes
  already exist.
- **Gains:** inherits every guarantee from the API automatically; **one**
  enforcement point instead of two kept in sync; removes the duplicate agent
  runtime, ChromaDB instance and embedding model; removes the second writer on
  the SQLite file.
- **Needs:** a way for the MCP caller to supply a token (see Option 4) — or, in
  the interim, a single service token, which is fine only when combined with
  Option 2's single-identity model.
- This is the option that stops the two surfaces drifting apart.

## Option 4 — MCP authorization spec

Recent revisions of the MCP specification define authorization for HTTP
transports, with the server acting as an OAuth 2.1 resource server and clients
presenting bearer tokens. Globus would be the authorization server.

- **Effort:** largest; check what the pinned `mcp` SDK version actually
  supports before committing to it.
- **Worth it only if** the MCP surface genuinely becomes multi-user.
- Pairs naturally with registering Cassiopeia as a **Globus resource server
  with its own scope** (`https://auth.globus.org/scopes/<client-id>/<scope>`),
  which is also what would let APPL-Agent call Cassiopeia on a researcher's
  behalf. See the Authentication section of the README.

---

## The trap to avoid

Do **not** give the MCP server one privileged token while keeping
`researcher_id` as a tool argument. That recreates the bug the `globus-auth`
branch fixed, one layer up, with the MCP server as a confused deputy — and it
would look secure, because a token would be involved.

Whatever is chosen: the identity must come from the credential, never from the
tool arguments.

---

## Suggested path

1. Option 1 now.
2. Option 2 when the MCP server is next actually used.
3. Option 3 when APPL-Agent integration becomes real — it is the one that
   collapses two enforcement points into one.
4. Option 4 only if MCP genuinely goes multi-user.

## Open questions

- Is APPL-Agent co-located with Cassiopeia, or remote? (Decides 2 vs 3.)
- Does one MCP server need to serve several researchers at once, or is a
  process per researcher acceptable?
- Should Cassiopeia be registered as a Globus resource server with its own
  scope? That is the prerequisite for any real delegation and is worth deciding
  once, for the API and MCP together.

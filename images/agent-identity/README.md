# agent-identity

Image of the agent identity sidecar: `token-proxy.py`, standard library only, on UBI 9
Python 3.12.

## Purpose

An agent pod calls `127.0.0.1:<PORT>` instead of the gateway. The proxy adds the pod's
projected ServiceAccount token (`TOKEN_FILE`) to every request, and under
`AGENT_TOKEN_PATHS` (default `/mcp`) the agent's own OpenID Connect token instead: a
`client_credentials` grant at `AGENT_TOKEN_URL`, authenticated with a second projected
ServiceAccount token (`AGENT_TOKEN_ASSERTION_FILE`) as client assertion, cached until
shortly before it expires. Response bodies stream through, so model answers (SSE) stay live.

| Variable | Default | Meaning |
|---|---|---|
| `UPSTREAM` | required | Gateway base URL |
| `PORT` | `8099` | Listen port on 127.0.0.1 |
| `TOKEN_FILE` | `/var/run/secrets/api-token/token` | Projected token for the gateway |
| `AGENT_TOKEN_URL` | empty: off | Token endpoint of the OpenID Connect provider |
| `AGENT_TOKEN_ASSERTION_FILE` | `/var/run/secrets/agent-token/token` | Projected token whose audience is the provider |
| `AGENT_TOKEN_PATHS` | `/mcp` | Comma list of path prefixes that carry the agent token |

`GET /healthz` answers 200 while the ServiceAccount token is readable.

## Build

The image is built once by a BuildConfig that pins a commit of this repository, context
`images/agent-identity`. The charts' `files/token-proxy.py` copies must stay byte-identical
to this file (`ci/check.sh`) until they use the injected sidecar.

## Test

```bash
python3 -m unittest images/agent-identity/test_token_proxy.py
```

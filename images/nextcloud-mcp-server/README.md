# nextcloud-mcp-server image

The image `ghcr.io/hoolia/nextcloud-mcp-server` is the default image of the chart `mcp-nextcloud`: upstream [cbcoutinho/nextcloud-mcp-server](https://github.com/cbcoutinho/nextcloud-mcp-server) 0.201.2 with one patch.

- `patch/patch.py` adds a bearer passthrough to `multi_user_basic` mode: a caller's `Authorization: Bearer` token goes to Nextcloud unchanged, where the `user_oidc` app validates it. Basic with an app password keeps working. Upstream releases up to 0.202.1 pass no bearer token through.
- The patch fails the build when an edited block no longer matches the upstream code, so a new upstream base needs a reviewed patch.
- `VERSION` is the image tag: `<upstream version>-bearer.<patch revision>`. The workflow `.github/workflows/images.yaml` builds every pull request and pushes a new `VERSION` from `main`; a pushed tag is never rebuilt.

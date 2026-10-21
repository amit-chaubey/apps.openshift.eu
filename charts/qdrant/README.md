# qdrant

## Purpose

Qdrant is a vector database: it stores embeddings for retrieval-augmented generation (RAG), semantic search and agent memory. The chart runs one upstream Qdrant in the release namespace with a data volume, behind an API key. Clients in the same project, and in namespaces you allow, reach it at port 6333 (HTTP) and 6334 (gRPC); there is no public address.

With `mcp.image` set, the chart also runs the official [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant) next to it: long-term memory tools for AI agents (`qdrant-find`, `qdrant-store`) on streamable HTTP. Its Service carries `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name`, so an MCP gateway with a label selector discovers it and the project's agents get the tools as `<targetName>_qdrant-find` and `<targetName>_qdrant-store`. Uninstalling the release removes the tools again; agents need no change either way. The tool descriptions tell the model when to search and when to save.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install memory grncloud/qdrant \
  --set mcp.image.repository=<registry>/mcp-server-qdrant --set mcp.image.tag=<tag>
```

- `persistence.size` (default: 5Gi): size of the data volume.
- `persistence.storageClass` (default: empty): StorageClass of the data volume. Empty: the cluster default.
- `secrets.type` (default: kubernetes): `externalSecret` reads the API key from a secret store.
- `secrets.apiKey` (default: empty): API key. Empty: generated at the first install and kept on upgrade.
- `connection.configMap` (default: true): ConfigMap `<release>-qdrant-connection` with `QDRANT_URL`, `QDRANT_GRPC_URL` and the name of the key Secret.
- `networkPolicy.ingressNamespaces` (default: empty): label selectors of further namespaces that may connect to Qdrant.
- `mcp.enabled` (default: true): run the memory tools. They render only with `mcp.image.repository` and `mcp.image.tag` set.
- `mcp.image` (default: empty): an image of mcp-server-qdrant. Upstream publishes none; build it from the repository's Dockerfile, with the embedding model downloaded at build time.
- `mcp.collection` (default: memory): collection that holds the memories.
- `mcp.targetName` (default: memory): tool prefix on the gateway.
- `mcp.findDescription`, `mcp.storeDescription`: tool descriptions; the defaults say to search at the start of every task and to save decisions, preferences and lessons learned.
- `mcp.ingressNamespaces` (default: empty): label selectors of the namespaces that may call the tools, for example the gateway's.
- `mcp.allowInternet` (default: false): let the tool server reach the internet, for an image that downloads its embedding model at start.

Clients of Qdrant itself send the key from Secret `<release>-qdrant-api-key` (key `api-key`) in the `api-key` header:

```bash
curl -H "api-key: $QDRANT_API_KEY" "$QDRANT_URL/collections"
```

A tool that renders the chart without access to the cluster, such as Argo CD or `helm template`, generates a new key on every render: set `secrets.apiKey` there, or use `secrets.type=externalSecret`.

## Delivery instructions

- Install one release per project that needs its own vectors; data is not shared between releases, so each team's agents share one memory.
- `helm uninstall` keeps the data volume (a StatefulSet volume claim). Delete the claim `data-<release>-qdrant-0` to remove the data.
- With `secrets.type=externalSecret`, create the vault item named in `secrets.externalSecret.items.apiKey` (default `<release>-qdrant-api-key`) with the key in the password field, and set `secrets.externalSecret.storeName` to your store.
- The tool server has no login of its own: anyone who reaches its port can read and write the memory. Keep `mcp.ingressNamespaces` to the gateway, and let the gateway authenticate callers.

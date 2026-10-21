# qdrant

## Purpose

Qdrant is a vector database: it stores embeddings for retrieval-augmented generation (RAG), semantic search and agent memory. The chart runs one upstream Qdrant in the release namespace with a data volume, behind an API key. Clients in the same project, and in namespaces you allow, reach it at port 6333 (HTTP) and 6334 (gRPC); there is no public address.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install memory grncloud/qdrant
```

- `persistence.size` (default: 5Gi): size of the data volume.
- `persistence.storageClass` (default: empty): StorageClass of the data volume. Empty: the cluster default.
- `secrets.type` (default: kubernetes): `externalSecret` reads the API key from a secret store.
- `secrets.apiKey` (default: empty): API key. Empty: generated at the first install and kept on upgrade.
- `connection.configMap` (default: true): ConfigMap `<release>-qdrant-connection` with `QDRANT_URL`, `QDRANT_GRPC_URL` and the name of the key Secret.
- `networkPolicy.ingressNamespaces` (default: empty): label selectors of further namespaces that may connect.

Clients send the key from Secret `<release>-qdrant-api-key` (key `api-key`) in the `api-key` header:

```bash
curl -H "api-key: $QDRANT_API_KEY" "$QDRANT_URL/collections"
```

A tool that renders the chart without access to the cluster, such as Argo CD or `helm template`, generates a new key on every render: set `secrets.apiKey` there, or use `secrets.type=externalSecret`.

## Delivery instructions

- Install one release per project that needs its own vectors; data is not shared between releases.
- `helm uninstall` keeps the data volume (a StatefulSet volume claim). Delete the claim `data-<release>-qdrant-0` to remove the data.
- With `secrets.type=externalSecret`, create the vault item named in `secrets.externalSecret.items.apiKey` (default `<release>-qdrant-api-key`) with the key in the password field, and set `secrets.externalSecret.storeName` to your store.

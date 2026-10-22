# KB Proxy Helm Chart for HPE AI Essentials

This chart deploys the existing KB Proxy Python application as a Kubernetes Deployment with a ClusterIP Service and an optional Istio VirtualService for the HPE AI Essentials gateway.

## Requirements
- A container image containing `kb_proxy.py`, built from the Dockerfile.
- Kubernetes with Istio CRDs and gateway `istio-system/ezaf-gateway` if the VirtualService is enabled.
- A DNS hostname and gateway TLS configuration that routes the chosen endpoint to the gateway.

## Install

```bash
helm upgrade --install kb-proxy ./kb-proxy \
  --namespace ezua-system --create-namespace \
  --set image.repository=registry.example.com/ai/kb-proxy \
  --set image.tag=latest \
  --set ezua.virtualService.endpoint=kb-proxy.app.example.com
```

Substitute the image repository, namespace and endpoint for your environment. The Helm chart does not create gateway TLS certificates or DNS records.

If the container registry requires a pull secret, configure `imagePullSecrets` in values.yaml. `env.SA_NAME` must refer to the RAG application accessible to the caller.

## Validate

```bash
helm lint ./kb-proxy --set image.repository=registry.example.com/ai/kb-proxy --set ezua.virtualService.endpoint=kb-proxy.app.example.com
helm template kb-proxy ./kb-proxy --namespace ezua-system --set image.repository=registry.example.com/ai/kb-proxy --set ezua.virtualService.endpoint=kb-proxy.app.example.com
```

## Port settings

When changing port, set `env.PORT` and `containerPort` to the same number. `service.port` can differ because it forwards to the named container port.

## Security and readiness

The `/health` probe checks KB Proxy process availability, not the health of the upstream RAG Coordinator. `/v1/models` in the application calls the upstream nonexistent endpoint and interprets a 404; a 404 is *not* proof of successful authentication unless authentication precedes route matching at the upstream gateway.

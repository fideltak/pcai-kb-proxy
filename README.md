# HPE PCAI Knowledge Base Proxy
A proxy between KB and OpenAI API compatible tools.

![](pics/owui.png)

## HELM Values
| Parameter | Default | Required | Description |
|---|---|---|---|
| env.BASE_URL |  | false | Base URL of the PCAI RAG Coordinator.(i.e. https://rag-coordinator.app.dev.example.com) |
| env.HOST | 0.0.0.0 | false | Network address on which KB Proxy listens. |
| env.PORT | 8080 | false | HTTP listening port of KB Proxy. |
| env.VERIFY_SSL | false | false | Enable SSL certificate verification for RAG Coordinator connections. |
| env.TIMEOUT | 300 | false | Request timeout in seconds for RAG Coordinator. |
| env.SA_NAME |  | false | Service Application name used by RAG Coordinator. (i.e. test-20261021-2-1792567478081) |
| env.ENABLE_CITATIONS | true | false | Enable citations in RAG Coordinator responses. |
| env.MODEL_NAME |  | true | LLM model name sent to RAG Coordinator. (i.e. Qwen/Qwen3-14B) |
| env.PROXY_NAME | HPE-PCAI-KB-PROXY | false | Model name exposed through the OpenAI-compatible API. |
| ezua.virtualService.enabled | true | true | Enable Istio VirtualService creation. |
| ezua.virtualService.endpoint |   | true | External hostname used to access KB Proxy. |
| ezua.virtualService.istioGateway | istio-system/ezaf-gateway | true | Istio Gateway used by HPE AI Essentials. |
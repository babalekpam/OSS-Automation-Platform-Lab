# Deployment guide

## Local (no containers)
```bash
make bootstrap
make test
make run-n      # :8080, release n  (docs at /docs)
make run-n1     # :8081, release n1
make validate-remote N_URL=http://localhost:8080 N1_URL=http://localhost:8081
```
`make validate` runs both releases in-process, which is what CI does.

## Containers
`docker compose up -d` builds one image and starts N on :8080 and N+1 on :8081. The Dockerfile and
compose file have not been exercised in CI yet.

## Not yet provided
Kubernetes/Helm charts, NetBox, Kafka, Temporal, containerlab topologies, Prometheus/Loki/Grafana, and
Argo CD. See the README roadmap.

# Roadmap

This repository is currently a source snapshot of a production Helm chart. The
next milestone is to make external deployments a supported first-class use case.

## Toward external deployability

1. **Externalize provider settings**
   - domain, ingress class, TLS issuer, storage classes
   - OIDC provider endpoints and scopes
   - LLM endpoint and API-key Secret names
   - S3 endpoint, bucket names, and regions
   - optional HTTP proxy settings

2. **Make components optional**
   - GitLab provisioning
   - Pipe Control and the content pipeline
   - public status site
   - monitoring and logging
   - interim PVC backups

3. **Publish or replace private images**
   - Pipe Control image
   - public site image
   - or document a bring-your-own-image contract

4. **Add a generic test path**
   - render and deploy on a disposable local cluster (kind/k3d/minikube)
   - verify a single-Course stack with a local PostgreSQL or CNPG installation
   - run the same checks in CI

5. **Split deployment configuration**
   - public chart repository = generic code and examples
   - private deployment repository = production values, Secret references,
     environment overlays, and internal runbooks

## Non-goals for the first public snapshot

- reproducing the original platform's exact identity, LLM, or storage services
- shipping production configuration or private images
- promising a turnkey installation experience

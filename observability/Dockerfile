# Grafana with Loki (logs), Tempo (traces), Prometheus (metrics) and an OTLP
# receiver, plus the GujjuTours dashboard built in. Deployed as its own Railway
# service (Root Directory /observability); see docs/OBSERVABILITY.md.
#
# Login: GF_SECURITY_ADMIN_USER / GF_SECURITY_ADMIN_PASSWORD (service variables).
# Ports: 3000 Grafana UI (public), 4318 OTLP HTTP and 4317 OTLP gRPC (private).
FROM grafana/otel-lgtm:0.8.1

# The base image starts Grafana with anonymous Admin access (fine on a laptop,
# not on a public URL). Turn it off so only GF_SECURITY_ADMIN_* can log in.
RUN sed -i 's/^export GF_AUTH_ANONYMOUS_ENABLED=true/export GF_AUTH_ANONYMOUS_ENABLED=false/; /^export GF_AUTH_ANONYMOUS_ORG_ROLE=/d' /otel-lgtm/run-grafana.sh  && grep -q 'GF_AUTH_ANONYMOUS_ENABLED=false' /otel-lgtm/run-grafana.sh

COPY grafana-dashboard.json /otel-lgtm/gujjutours-dashboard.json
COPY grafana-provisioning.yaml /otel-lgtm/grafana/conf/provisioning/dashboards/gujjutours.yaml

EXPOSE 3000 4317 4318

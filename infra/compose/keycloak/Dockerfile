# Production Keycloak image: bake the PostgreSQL provider selection and health support once, then
# run the immutable optimized server. The PostgreSQL driver is already bundled by Keycloak.
FROM quay.io/keycloak/keycloak:26.7 AS builder

ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true

RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.7

COPY --from=builder /opt/keycloak/ /opt/keycloak/

ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]

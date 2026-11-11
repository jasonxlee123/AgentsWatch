\set ON_ERROR_STOP on

BEGIN;

-- Public addresses may represent a shared NAT or reverse-proxy egress and are
-- therefore attributes, not server identities. Keep an index for filtering,
-- but do not reject multiple active servers that share the same public IP.
DROP INDEX IF EXISTS asset.uq_server_public_ip_current;

CREATE INDEX IF NOT EXISTS ix_server_public_ip_current
    ON asset.server_asset (environment_id, public_ip)
    WHERE public_ip IS NOT NULL AND lifecycle_status <> 'retired';

COMMIT;

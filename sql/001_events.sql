CREATE TABLE IF NOT EXISTS events (
    event_id text PRIMARY KEY,
    region text NOT NULL,
    vehicle_id text NOT NULL,
    speed_kmh double precision CHECK (speed_kmh BETWEEN 0 AND 400),
    payload jsonb NOT NULL DEFAULT '{}',
    site text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_region_received_at_desc
    ON events (region, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_vehicle_id_received_at_desc
    ON events (vehicle_id, received_at DESC);

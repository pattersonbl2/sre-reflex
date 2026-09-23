CREATE TABLE alerts (
    id bigserial PRIMARY KEY,
    fingerprint text NOT NULL,
    alertname text NOT NULL,
    labels jsonb NOT NULL,
    fired_at timestamptz NOT NULL,
    resolved_at timestamptz,
    UNIQUE (fingerprint, fired_at)
);

CREATE TABLE states (
    id bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES alerts(id),
    text text NOT NULL,
    collectors_ok jsonb NOT NULL,
    token_count int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE decisions (
    id bigserial PRIMARY KEY,
    state_id bigint NOT NULL REFERENCES states(id),
    model text NOT NULL,
    question_id text NOT NULL,
    q_version int NOT NULL,
    value text NOT NULL,
    distribution jsonb NOT NULL,
    latency_ms int NOT NULL,
    cost_usd numeric NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE labels (
    id bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES alerts(id),
    value text NOT NULL CHECK (value IN ('real', 'noise')),
    source text NOT NULL CHECK (source IN ('hand', 'inferred')),
    sig text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX decisions_state_id_idx ON decisions (state_id);
CREATE INDEX labels_alert_id_idx ON labels (alert_id);

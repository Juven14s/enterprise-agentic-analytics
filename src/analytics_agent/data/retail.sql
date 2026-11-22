CREATE TABLE analytics.customers (
    customer_id INTEGER PRIMARY KEY,
    region VARCHAR(40) NOT NULL
);

CREATE TABLE analytics.orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES analytics.customers(customer_id),
    order_date DATE NOT NULL,
    status VARCHAR(15) NOT NULL CHECK (status IN ('completed', 'cancelled', 'pending')),
    net_amount_cents INTEGER NOT NULL CHECK (net_amount_cents >= 0)
);

INSERT INTO analytics.customers (customer_id, region) VALUES
    (1, 'Nord'), (2, 'Sud'), (3, 'Ouest'), (4, 'Nord'), (5, 'Sud');

INSERT INTO analytics.orders (order_id, customer_id, order_date, status, net_amount_cents) VALUES
    (1, 1, '2025-01-05', 'completed', 12000),
    (2, 2, '2025-01-12', 'completed', 8000),
    (3, 3, '2025-01-24', 'cancelled', 9000),
    (4, 4, '2025-02-03', 'completed', 15000),
    (5, 2, '2025-02-10', 'completed', 11000),
    (6, 3, '2025-02-21', 'completed', 6000),
    (7, 5, '2025-03-02', 'pending', 20000),
    (8, 1, '2025-03-11', 'completed', 14000),
    (9, 3, '2025-03-19', 'completed', 9500),
    (10, 2, '2025-03-30', 'cancelled', 4000);

-- GenAI Sports store (portable PostgreSQL schema, used in local mode).
-- The AlloyDB variant (alloydb_extras.sql) adds pgvector embeddings + AlloyDB AI.
CREATE DATABASE store;
\connect store

CREATE TABLE users (
    user_id     SERIAL PRIMARY KEY,
    first_name  VARCHAR(100) NOT NULL,
    last_name   VARCHAR(100),
    email       VARCHAR(255) UNIQUE NOT NULL,
    address     VARCHAR(255),
    city        VARCHAR(100),
    postal_code VARCHAR(20),
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION
);

CREATE TABLE products (
    product_id       SERIAL PRIMARY KEY,
    name             VARCHAR(255) UNIQUE NOT NULL,
    description      TEXT,
    category         VARCHAR(100),
    brand            VARCHAR(100),
    image_url        VARCHAR(512),
    popularity_score DOUBLE PRECISION DEFAULT 0,
    search_vector    TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(brand, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(category, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')) STORED
);
CREATE INDEX products_search_idx ON products USING GIN (search_vector);

CREATE TABLE products_variants (
    variant_id SERIAL PRIMARY KEY,
    product_id INT NOT NULL REFERENCES products(product_id),
    size       VARCHAR(50) NOT NULL,
    color      VARCHAR(50) NOT NULL,
    price      NUMERIC(10,2) NOT NULL CHECK (price >= 0),
    UNIQUE (product_id, size, color)
);

CREATE TABLE stores (
    store_id    SERIAL PRIMARY KEY,
    name        VARCHAR(255) UNIQUE NOT NULL,
    address     VARCHAR(255),
    city        VARCHAR(100),
    postal_code VARCHAR(20),
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION
);

CREATE TABLE inventory (
    variant_id INT NOT NULL REFERENCES products_variants(variant_id),
    store_id   INT NOT NULL REFERENCES stores(store_id),
    quantity   INT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    PRIMARY KEY (variant_id, store_id)
);

CREATE TABLE delivery_methods (
    delivery_method_id      SERIAL PRIMARY KEY,
    store_id                INT NOT NULL REFERENCES stores(store_id),
    name                    VARCHAR(100) NOT NULL,
    description             TEXT,
    base_cost               NUMERIC(10,2) NOT NULL DEFAULT 0,
    estimated_delivery_time VARCHAR(100),
    UNIQUE (store_id, name)
);

CREATE TABLE shopping_lists (
    list_id SERIAL PRIMARY KEY,
    user_id INT UNIQUE NOT NULL REFERENCES users(user_id)
);

CREATE TABLE shopping_list_items (
    item_id    SERIAL PRIMARY KEY,
    list_id    INT NOT NULL REFERENCES shopping_lists(list_id) ON DELETE CASCADE,
    variant_id INT NOT NULL REFERENCES products_variants(variant_id),
    quantity   INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    UNIQUE (list_id, variant_id)
);

CREATE TABLE orders (
    order_id           SERIAL PRIMARY KEY,
    user_id            INT NOT NULL REFERENCES users(user_id),
    store_id           INT NOT NULL REFERENCES stores(store_id),
    delivery_method_id INT REFERENCES delivery_methods(delivery_method_id),
    order_status       VARCHAR(50) NOT NULL DEFAULT 'pending',
    shipping_address   VARCHAR(255),
    shipping_cost      NUMERIC(10,2) NOT NULL DEFAULT 0,
    total_amount       NUMERIC(12,2) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    order_item_id   SERIAL PRIMARY KEY,
    order_id        INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    variant_id      INT NOT NULL REFERENCES products_variants(variant_id),
    quantity        INT NOT NULL CHECK (quantity > 0),
    price_at_purchase NUMERIC(10,2) NOT NULL,
    product_name    VARCHAR(255) NOT NULL,
    variant_details TEXT
);

-- Great-circle distance in meters (PostGIS replacement for local mode).
CREATE FUNCTION distance_m(lat1 DOUBLE PRECISION, lon1 DOUBLE PRECISION,
                           lat2 DOUBLE PRECISION, lon2 DOUBLE PRECISION)
RETURNS DOUBLE PRECISION LANGUAGE SQL IMMUTABLE AS $$
  SELECT 2 * 6371000 * asin(sqrt(
           power(sin(radians(lat2 - lat1) / 2), 2) +
           cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)));
$$;

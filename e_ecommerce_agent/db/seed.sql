\connect store

INSERT INTO users (first_name, last_name, email, address, city, postal_code, latitude, longitude) VALUES
('Alex',  'Rivera', 'alex.rivera@example.com', '1 Washington Sq', 'San Jose',      '95192', 37.3352, -121.8811),
('Priya', 'Nair',   'priya.nair@example.com',  '500 Castro St',   'Mountain View', '94041', 37.3894, -122.0819),
('Sam',   'Lee',    'sam.lee@example.com',     '200 Market St',   'San Francisco', '94105', 37.7936, -122.3965);

INSERT INTO products (name, description, category, brand, popularity_score) VALUES
('Pegasus 41',        'Responsive everyday running shoe with ReactX foam, great for daily training and half marathons.', 'Running', 'Nike', 0.95),
('Ultraboost Light',  'Lightweight cushioned road running shoe with Light BOOST midsole for long comfortable runs.', 'Running', 'Adidas', 0.90),
('Speedgoat 6',       'Trail running shoe with aggressive Vibram lugs and plush cushioning for ultra-trail races and technical mountain terrain.', 'Running', 'Hoka', 0.93),
('Gel-Nimbus 26',     'Max-cushion neutral running shoe with FF BLAST+ foam for marathon training and recovery runs.', 'Running', 'ASICS', 0.88),
('Cloudmonster 2',    'High-stack running shoe with CloudTec Phase for a rocking, energetic ride on road runs.', 'Running', 'On', 0.84),
('Osprey Atmos AG 65','Backpacking pack with Anti-Gravity suspension and ventilated back panel for multi-day hikes.', 'Hiking', 'Osprey', 0.86),
('Quechua MH500 Boots','Waterproof mid hiking boots with grippy sole for rainy mountain hiking.', 'Hiking', 'Quechua', 0.75),
('Patagonia Torrentshell 3L','Packable waterproof breathable rain jacket for hiking and travel.', 'Hiking', 'Patagonia', 0.89),
('Trek Marlin 7',     'Hardtail mountain bike with RockShox fork and hydraulic disc brakes for singletrack trails.', 'Cycling', 'Trek', 0.82),
('Giro Syntax MIPS',  'Ventilated road cycling helmet with MIPS brain protection.', 'Cycling', 'Giro', 0.78),
('Wilson Clash 100',  'Flexible tennis racket with large sweet spot, ideal for intermediate players with arm comfort needs.', 'Tennis', 'Wilson', 0.80),
('Babolat Pure Drive','Powerful tennis racket for baseline players who want spin and depth.', 'Tennis', 'Babolat', 0.83),
('Manduka PRO Yoga Mat','Dense 6mm yoga mat with lifetime guarantee and non-slip surface for daily practice.', 'Yoga', 'Manduka', 0.87),
('Lululemon Align Leggings','Buttery-soft high-rise leggings for yoga and low-impact training.', 'Yoga', 'Lululemon', 0.91),
('Garmin Forerunner 265','GPS running watch with AMOLED display, training readiness and marathon race predictor.', 'Running', 'Garmin', 0.92),
('Nike Dri-FIT Running Tee','Sweat-wicking lightweight running t-shirt for hot weather runs.', 'Running', 'Nike', 0.77);

-- variants: running shoes in sizes 8-11, other products in sizes / one size
INSERT INTO products_variants (product_id, size, color, price)
SELECT p.product_id, s.size, c.color, x.base
FROM (VALUES ('Pegasus 41', 139.99), ('Ultraboost Light', 189.99), ('Speedgoat 6', 154.99),
             ('Gel-Nimbus 26', 159.99), ('Cloudmonster 2', 179.99)) AS x(name, base)
JOIN products p ON p.name = x.name
CROSS JOIN (VALUES ('8'), ('9'), ('10'), ('11')) AS s(size)
CROSS JOIN (VALUES ('Black/White'), ('Blue')) AS c(color);

INSERT INTO products_variants (product_id, size, color, price)
SELECT p.product_id, v.size, v.color, v.price
FROM (VALUES
  ('Osprey Atmos AG 65', 'M', 'Venom Green', 340.00), ('Osprey Atmos AG 65', 'L', 'Venom Green', 340.00),
  ('Quechua MH500 Boots', '9', 'Brown', 89.99), ('Quechua MH500 Boots', '10', 'Brown', 89.99),
  ('Patagonia Torrentshell 3L', 'M', 'Black', 179.00), ('Patagonia Torrentshell 3L', 'L', 'Navy', 179.00),
  ('Trek Marlin 7', 'M', 'Matte Black', 1199.99), ('Trek Marlin 7', 'L', 'Matte Black', 1199.99),
  ('Giro Syntax MIPS', 'M', 'White', 149.95),
  ('Wilson Clash 100', 'Grip 2', 'Red/Black', 249.00), ('Wilson Clash 100', 'Grip 3', 'Red/Black', 249.00),
  ('Babolat Pure Drive', 'Grip 2', 'Blue', 239.00),
  ('Manduka PRO Yoga Mat', 'One Size', 'Black Magic', 129.00),
  ('Lululemon Align Leggings', 'S', 'Black', 98.00), ('Lululemon Align Leggings', 'M', 'Black', 98.00),
  ('Garmin Forerunner 265', 'One Size', 'Black', 449.99),
  ('Nike Dri-FIT Running Tee', 'M', 'Blue', 35.00), ('Nike Dri-FIT Running Tee', 'L', 'Black', 35.00)
) AS v(name, size, color, price)
JOIN products p ON p.name = v.name;

INSERT INTO stores (name, address, city, postal_code, latitude, longitude) VALUES
('GenAI Sports Downtown San Jose', '150 S 1st St',        'San Jose',      '95113', 37.3337, -121.8890),
('GenAI Sports Santana Row',       '377 Santana Row',     'San Jose',      '95128', 37.3209, -121.9480),
('GenAI Sports Palo Alto',         '855 El Camino Real',  'Palo Alto',     '94301', 37.4380, -122.1597),
('GenAI Sports SoMa',              '845 Market St',       'San Francisco', '94103', 37.7840, -122.4075);

INSERT INTO inventory (variant_id, store_id, quantity)
SELECT v.variant_id, s.store_id, (v.variant_id * 7 + s.store_id * 3) % 12
FROM products_variants v CROSS JOIN stores s;

INSERT INTO delivery_methods (store_id, name, description, base_cost, estimated_delivery_time)
SELECT s.store_id, d.name, d.description, d.cost, d.eta
FROM stores s CROSS JOIN (VALUES
  ('Standard Home Delivery', 'Ground shipping to your address.', 5.99, '3-5 business days'),
  ('Express Delivery',       'Priority courier delivery.',      14.99, '1 business day'),
  ('Store Pickup',           'Pick up at the store counter.',     0.00, 'Ready in 2 hours')
) AS d(name, description, cost, eta);

-- One row per shop with where it is: the ERP's store master joined with the reference
-- coordinates kept as code (seeds/city_coordinates.csv). The online shop has none.
select
    s.org_id as org_id, s.store_id as store_id, s.store_name as store_name, s.region as region,
    s.city as city, s.country as country, s.spot as spot, s.opened as opened, s.store_format as store_format,
    s.store_size as store_size,
    c.lat as lat, c.lon as lon
from {{ ref('dim_store') }} s
left join {{ ref('city_coordinates') }} c on c.city = s.city

{{ config(materialized='table') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'dim_store') }}
{% else %}
-- Shops from the ERP (CDC current state) plus one virtual "WEB" store per tenant for
-- home deliveries, so every sale has a store dimension.
select
    org_id,
    row ->> 'store_id' as store_id,
    row ->> 'name' as store_name,
    row ->> 'region' as region,
    row ->> 'city' as city,
    row ->> 'country' as country,
    row ->> 'spot' as spot,
    row ->> 'format' as store_format,
    row ->> 'size' as store_size,
    cast(row ->> 'opened' as date) as opened
from {{ source('bronze', 'cdc_stores') }}
where op <> 'DELETE'
union all
select distinct org_id, 'WEB', 'Online shop', 'Online', null, null, 'online', 'online', null, cast(null as date)
from {{ source('bronze', 'cdc_stores') }}
{% endif %}

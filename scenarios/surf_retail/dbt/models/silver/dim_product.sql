{{ config(materialized='table') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'dim_product') }}
{% else %}
-- Slowly changing dimension (type 2) built from the ERP products change log: a price change
-- captured by CDC opens a new version, so a sale is costed and priced as of its own time.
-- The first version of each product is valid from the beginning of time.
with changes as (
    select org_id, key as sku, op, row, captured_at, change_id,
           row_number() over (partition by org_id, key order by change_id) as n
    from {{ source('bronze', 'bronze_cdc_products') }}
    where op <> 'DELETE'
),
versions as (
    select
        org_id, sku,
        row ->> 'name' as product_name,
        row ->> 'category' as category,
        row ->> 'rider_group' as rider_group,
        row ->> 'brand' as brand,
        row ->> 'model' as model,
        row ->> 'size_label' as size_label,
        cast(row ->> 'length_in' as integer) as length_in,
        cast(row ->> 'volume_l' as numeric) as volume_l,
        row ->> 'construction' as construction,
        row ->> 'shape' as shape,
        row ->> 'colour' as colour,
        cast(row ->> 'launched' as date) as launched,
        cast(row ->> 'retired' as date) as retired,
        cast(row ->> 'price_cents' as integer) as list_price_cents,
        cast(row ->> 'cost_cents' as integer) as cost_cents,
        case when n = 1 then timestamptz '1900-01-01 00:00:00+00' else captured_at end as valid_from,
        change_id
    from changes
)
select
    org_id || ':' || sku || ':' || change_id as product_version_key,
    org_id, sku, product_name, category, rider_group, brand, model, size_label, length_in, volume_l, construction,
    shape, colour, launched, retired, list_price_cents, cost_cents, valid_from,
    coalesce(lead(valid_from) over (partition by org_id, sku order by change_id),
             timestamptz '9999-12-31 00:00:00+00') as valid_to,
    lead(valid_from) over (partition by org_id, sku order by change_id) is null as is_current
from versions
{% endif %}

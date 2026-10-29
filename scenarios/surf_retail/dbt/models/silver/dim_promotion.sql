{{ config(materialized='table') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'dim_promotion') }}
{% else %}
select
    org_id,
    row ->> 'promo_id' as promo_id,
    row ->> 'name' as promo_name,
    cast(row ->> 'pct' as integer) as discount_pct,
    row ->> 'channel' as promo_channel,
    cast(row ->> 'start_date' as date) as start_date,
    cast(row ->> 'end_date' as date) as end_date,
    row ->> 'skus' as skus
from {{ source('bronze', 'cdc_promotions') }}
where op <> 'DELETE'
{% endif %}

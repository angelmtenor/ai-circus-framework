{{ config(materialized='table') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'dim_promotion_sku') }}
{% else %}
select org_id, promo_id, trim(sku) as sku
from {{ ref('dim_promotion') }}
cross join lateral unnest(string_to_array(skus, ',')) as sku
{% endif %}

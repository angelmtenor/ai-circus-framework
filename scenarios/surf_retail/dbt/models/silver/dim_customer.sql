{{ config(enabled=not silver_source(), materialized='table') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'dim_customer') }}
{% else %}
-- Loyalty customers without any direct identifier: the key is an HMAC of the loyalty id,
-- names and e-mails never leave bronze.
select
    org_id,
    {{ pseudonymise("row ->> 'loyalty_id'") }} as customer_key,
    row ->> 'home_store_id' as home_store_id,
    cast(row ->> 'signup_date' as date) as signup_date
from {{ source('bronze', 'cdc_customers') }}
where op <> 'DELETE'
{% endif %}

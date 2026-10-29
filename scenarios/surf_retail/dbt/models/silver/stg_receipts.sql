{{ config(enabled=not silver_source(), materialized=('table' if silver_source() else 'incremental'), unique_key=['org_id', 'receipt_id'],
          incremental_strategy='delete+insert') }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'stg_receipts') }}
{% else %}
-- One row per receipt (streamed or from a legacy file), the loyalty id pseudonymised.
select
    b.org_id,
    b.business_key as receipt_id,
    b.payload ->> 'store_id' as store_id,
    b.payload ->> 'register_id' as register_id,
    b.event_ts,
    cast(b.event_ts as date) as day,
    case when b.payload ->> 'loyalty_id' is not null
         then {{ pseudonymise("b.payload ->> 'loyalty_id'") }} end as customer_key,
    cast(b.payload -> 'totals' ->> 'gross_cents' as bigint) as gross_cents,
    cast(b.payload -> 'totals' ->> 'discount_cents' as bigint) as discount_cents,
    cast(b.payload -> 'totals' ->> 'grand_total_cents' as bigint) as grand_total_cents,
    b.payload -> 'lines' as lines,
    b.source_kind,
    b.received_at as _received_at
from {{ source('bronze', 'bronze_pos_receipts') }} b
{% if is_incremental() %}
where {{ received_since('b.received_at') }}
{% endif %}
{% endif %}

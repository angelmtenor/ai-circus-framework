{{ config(materialized=('table' if silver_source() else 'incremental'), unique_key=['org_id', 'order_id'],
          incremental_strategy='delete+insert') }}
{% if silver_source() %}
select * except (lines) from {{ source('pg_silver', 'stg_web_orders') }}
{% else %}
-- One row per online order: its latest version wins, with when it was placed and collected.
with touched as (
    select distinct org_id, business_key
    from {{ source('bronze', 'bronze_web_orders') }}
    {% if is_incremental() %}
    where {{ received_since('received_at') }}
    {% endif %}
),
versions as (
    select b.*
    from {{ source('bronze', 'bronze_web_orders') }} b
    join touched t on t.org_id = b.org_id and t.business_key = b.business_key
),
latest as (
    select distinct on (org_id, business_key) *
    from versions
    order by org_id, business_key, version desc
),
history as (
    select org_id, business_key,
           min(event_ts) as created_ts,
           max(case when payload ->> 'status' in ('collected', 'delivered') then event_ts end) as completed_ts,
           max(received_at) as _received_at,
           count(*) as versions
    from versions
    group by org_id, business_key
)
select
    l.org_id,
    l.business_key as order_id,
    l.version,
    l.payload ->> 'status' as status,
    l.payload -> 'fulfilment' ->> 'type' as fulfilment,
    l.payload ->> 'store_id' as store_id,
    l.payload ->> 'return_reason' as return_reason,
    case when l.payload ->> 'customer_loyalty_id' is not null
         then {{ pseudonymise("l.payload ->> 'customer_loyalty_id'") }} end as customer_key,
    h.created_ts,
    cast(h.created_ts as date) as day,
    h.completed_ts,
    cast(l.payload -> 'totals' ->> 'lines_total_cents' as bigint) as lines_total_cents,
    cast(l.payload -> 'totals' ->> 'shipping_cents' as bigint) as shipping_cents,
    cast(l.payload -> 'totals' ->> 'grand_total_cents' as bigint) as grand_total_cents,
    l.payload -> 'lines' as lines,
    h.versions,
    h._received_at
from latest l
join history h on h.org_id = l.org_id and h.business_key = l.business_key
{% endif %}

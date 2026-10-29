{#- ClickHouse copies this (the biggest silver table) a year at a time: its PostgreSQL engine
    reads a whole result into memory, but pushes a plain `day` range down to Postgres. -#}
{%- set yearly_copy = [] -%}
{%- if silver_source() -%}
  {%- for year in range(2015, 2036) -%}
    {%- do yearly_copy.append("insert into {{ this }} select * from {{ source('pg_silver', 'fct_sales_line') }}"
        ~ " where day >= '" ~ year ~ "-01-01' and day < '" ~ (year + 1) ~ "-01-01'") -%}
  {%- endfor -%}
{%- endif -%}
{{ config(materialized=('table' if silver_source() else 'incremental'), unique_key=['org_id', 'doc_id'],
          incremental_strategy='delete+insert', post_hook=yearly_copy) }}
{% if silver_source() %}
select * from {{ source('pg_silver', 'fct_sales_line') }} where 1 = 0
{% else %}
-- One sold line across channels: store receipts and online orders, with the product version
-- valid at the time of sale (SCD2) for its category and cost. Cancelled orders stay, flagged by
-- `order_status` (an order cancelled later must replace its earlier lines); gold excludes them.
with docs as (
    select org_id, receipt_id as doc_id, 'store' as channel, 'carry_out' as fulfilment, store_id,
           cast(null as text) as order_status, customer_key, event_ts, day, lines, _received_at
    from {{ ref('stg_receipts') }}
    {% if is_incremental() %}
    where {{ received_since('_received_at') }}
    {% endif %}
    union all
    select org_id, order_id, 'online', fulfilment, coalesce(store_id, 'WEB'), status, customer_key, created_ts,
           day, lines, _received_at
    from {{ ref('stg_web_orders') }}
    {% if is_incremental() %}
    where {{ received_since('_received_at') }}
    {% endif %}
),
lines as (
    select d.*,
           cast(l ->> 'line_no' as integer) as line_no,
           l ->> 'sku' as sku,
           cast(l ->> 'qty' as integer) as qty,
           cast(l ->> 'unit_price_cents' as bigint) as unit_price_cents,
           cast(l ->> 'discount_cents' as bigint) as discount_cents,
           l ->> 'promo_id' as promo_id,
           cast(l ->> 'tax_rate' as numeric) as tax_rate,
           cast(l ->> 'line_total_cents' as bigint) as net_cents
    from docs d
    cross join lateral jsonb_array_elements(d.lines) as l
)
select
    l.org_id || ':' || l.doc_id || '#' || l.line_no as sales_line_id,
    l.org_id, l.doc_id, l.line_no, l.channel, l.fulfilment, l.order_status, l.store_id,
    coalesce(s.store_name, l.store_id) as store_name,
    coalesce(s.region, 'Unknown') as region,
    l.sku, p.product_name, p.category, coalesce(p.rider_group, 'accessory') as rider_group, l.promo_id,
    l.customer_key,
    l.event_ts, l.day, {{ hour_of_day('l.event_ts') }} as hour_of_day,
    l.qty,
    l.unit_price_cents * l.qty as gross_cents,
    l.discount_cents,
    l.net_cents,
    cast(round(l.net_cents / (1 + l.tax_rate)) as bigint) as net_ex_tax_cents,
    cast(coalesce(p.cost_cents, 0) as bigint) * l.qty as cost_cents,
    cast(round(l.net_cents / (1 + l.tax_rate)) as bigint) - cast(coalesce(p.cost_cents, 0) as bigint) * l.qty
        as margin_cents,
    l._received_at
from lines l
left join {{ ref('dim_store') }} s on s.org_id = l.org_id and s.store_id = l.store_id
left join {{ ref('dim_product') }} p
    on p.org_id = l.org_id and p.sku = l.sku and l.event_ts >= p.valid_from and l.event_ts < p.valid_to
{% endif %}

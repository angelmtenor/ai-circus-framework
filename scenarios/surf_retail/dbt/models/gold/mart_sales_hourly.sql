-- The intraday shape: sales per hour, store and channel.
select
    org_id,
    {{ hour_start('event_ts') }} as hour,
    hour_of_day,
    store_id, region, channel,
    count(distinct doc_id) as receipts,
    sum(net_cents) as revenue_cents
from {{ ref('fct_sales_line') }}
where coalesce(order_status, '') <> 'cancelled'
group by org_id, {{ hour_start('event_ts') }}, hour_of_day, store_id, region, channel

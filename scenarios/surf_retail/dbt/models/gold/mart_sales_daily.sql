-- Revenue, units, receipts, basket and margin per day, store and channel.
select
    org_id, day, store_id, store_name, region, channel,
    count(distinct doc_id) as receipts,
    sum(qty) as units,
    sum(net_cents) as revenue_cents,
    sum(discount_cents) as discount_cents,
    sum(net_ex_tax_cents) as net_ex_tax_cents,
    sum(margin_cents) as margin_cents,
    count(distinct case when customer_key is not null then doc_id end) as identified_receipts
from {{ ref('fct_sales_line') }}
where coalesce(order_status, '') <> 'cancelled'
group by org_id, day, store_id, store_name, region, channel

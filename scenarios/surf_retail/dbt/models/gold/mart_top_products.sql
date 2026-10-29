-- Units and revenue per week, region and product (store managers can't read it: no store column).
select
    org_id,
    {{ week_start('day') }} as week,
    region, sku,
    max(product_name) as product_name,
    max(category) as category,
    sum(qty) as units,
    sum(net_cents) as revenue_cents
from {{ ref('fct_sales_line') }}
where coalesce(order_status, '') <> 'cancelled'
group by org_id, {{ week_start('day') }}, region, sku

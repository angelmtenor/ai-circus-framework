-- Units, revenue and margin per week, shop, channel and product category — five years of the
-- chain's seasonality, and the grain of the demand forecast's training data.
select
    org_id,
    {{ week_start('day') }} as week,
    store_id, max(store_name) as store_name, max(region) as region, channel,
    rider_group, category,
    sum(qty) as units,
    sum(net_cents) as revenue_cents,
    sum(margin_cents) as margin_cents,
    count(distinct doc_id) as baskets
from {{ ref('fct_sales_line') }}
where coalesce(order_status, '') <> 'cancelled'
group by org_id, {{ week_start('day') }}, store_id, channel, rider_group, category

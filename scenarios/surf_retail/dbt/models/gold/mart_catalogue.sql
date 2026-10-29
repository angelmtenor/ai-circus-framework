-- The current catalogue as the web shop shows it: every product's current version with its
-- attributes (the drawings use shape, colour and length), whether it is still on sale, and
-- how it sold over the last 90 days of data (rank within its category = "best sellers").
with recent as (
    select f.org_id as org_id, f.sku as sku, sum(f.qty) as units_90d, sum(f.net_cents) as revenue_90d_cents
    from {{ ref('fct_sales_line') }} f
    join (select org_id, max(day) as last_day from {{ ref('fct_sales_line') }} group by org_id) m
        on m.org_id = f.org_id
    where f.day > {{ add_days('m.last_day', -90) }} and coalesce(f.order_status, '') <> 'cancelled'
    group by f.org_id, f.sku
)
select
    p.org_id as org_id, p.sku as sku, p.product_name as product_name, p.category as category,
    p.rider_group as rider_group, p.brand as brand, p.model as model, p.size_label as size_label,
    p.length_in as length_in, p.volume_l as volume_l, p.construction as construction, p.shape as shape,
    p.colour as colour, p.launched as launched, p.retired as retired, p.list_price_cents as list_price_cents,
    p.retired is null as on_sale,
    coalesce(r.units_90d, 0) as units_90d,
    coalesce(r.revenue_90d_cents, 0) as revenue_90d_cents,
    row_number() over (partition by p.org_id, p.category order by coalesce(r.units_90d, 0) desc, p.sku)
        as category_rank
from {{ ref('dim_product') }} p
left join recent r on r.org_id = p.org_id and r.sku = p.sku
where p.is_current

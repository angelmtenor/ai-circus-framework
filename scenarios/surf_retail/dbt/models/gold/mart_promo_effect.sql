-- Promoted lines per promotion, day, store and channel, next to the baseline: the same
-- products' average daily units in that store *and channel* over the 14 days before the
-- promotion started (an online-only promotion is compared with online sales only).
with promoted as (
    select org_id, promo_id, day, store_id, channel,
           sum(qty) as units, sum(net_cents) as revenue_cents, sum(discount_cents) as discount_cents,
           sum(margin_cents) as margin_cents
    from {{ ref('fct_sales_line') }}
    where promo_id is not null and coalesce(order_status, '') <> 'cancelled'
    group by org_id, promo_id, day, store_id, channel
),
before_promo as (
    -- Explicit aliases: ClickHouse would otherwise name these columns `p.org_id`, `f.store_id`…
    select p.org_id as org_id, p.promo_id as promo_id, f.store_id as store_id, f.channel as channel,
           sum(f.qty) as units_before
    from {{ ref('dim_promotion') }} p
    join {{ ref('dim_promotion_sku') }} ps on ps.org_id = p.org_id and ps.promo_id = p.promo_id
    join {{ ref('fct_sales_line') }} f on f.org_id = p.org_id and f.sku = ps.sku
    where f.day >= {{ add_days('p.start_date', -14) }} and f.day < p.start_date
      and coalesce(f.order_status, '') <> 'cancelled'
    group by p.org_id, p.promo_id, f.store_id, f.channel
)
select
    pr.org_id as org_id, pr.promo_id as promo_id, d.promo_name as promo_name, d.discount_pct as discount_pct,
    pr.day as day, pr.store_id as store_id, pr.channel as channel, pr.units as units,
    pr.revenue_cents as revenue_cents, pr.discount_cents as discount_cents, pr.margin_cents as margin_cents,
    coalesce(b.units_before, 0) / 14.0 as baseline_units
from promoted pr
join {{ ref('dim_promotion') }} d on d.org_id = pr.org_id and d.promo_id = pr.promo_id
left join before_promo b
    on b.org_id = pr.org_id and b.promo_id = pr.promo_id and b.store_id = pr.store_id and b.channel = pr.channel

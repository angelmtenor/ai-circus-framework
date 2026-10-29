{{ config(materialized='table') }}
{% if target.type == 'clickhouse' %}
-- A self-join of a year of baskets does not fit ClickHouse's per-query memory, and the result
-- (a few hundred rules) gains nothing from being recomputed: copy what Postgres built.
select * from {{ source('pg_gold', 'mart_product_affinity') }}
{% else %}
-- "Riders also bought": association rules over the last year of baskets (store receipts and
-- online orders) — what riders buy together now. Only baskets of two or more products make pairs.
-- For each product, the products found in the same basket most often
-- relative to how common they are anyway (lift), with the evidence behind each rule — the
-- web shop's recommender and the assistant's "what goes with this?" both read it.
with lines as (
    select distinct f.org_id as org_id, f.doc_id as doc_id, f.sku as sku
    from {{ ref('fct_sales_line') }} f
    join (select org_id, max(day) as last_day from {{ ref('fct_sales_line') }} group by org_id) m
        on m.org_id = f.org_id
    where f.day > {{ add_days('m.last_day', -365) }} and coalesce(f.order_status, '') <> 'cancelled'
),
multi as (
    select org_id, doc_id from lines group by org_id, doc_id having count(*) >= 2
),
paired as (
    select l.org_id as org_id, l.doc_id as doc_id, l.sku as sku
    from lines l join multi m on m.org_id = l.org_id and m.doc_id = l.doc_id
),
baskets as (
    select org_id, count(distinct doc_id) as n from lines group by org_id
),
items as (
    select org_id, sku, count(*) as docs from lines group by org_id, sku
),
pairs as (
    select a.org_id as org_id, a.sku as sku, b.sku as rec_sku, count(*) as together
    from paired a
    join paired b on b.org_id = a.org_id and b.doc_id = a.doc_id and b.sku <> a.sku
    group by a.org_id, a.sku, b.sku
    having count(*) >= 5
),
rules as (
    select
        p.org_id as org_id, p.sku as sku, p.rec_sku as rec_sku, p.together as together,
        i.docs as sku_baskets,
        1.0 * p.together / i.docs as confidence,
        (1.0 * p.together / i.docs) / (1.0 * r.docs / b.n) as lift
    from pairs p
    join items i on i.org_id = p.org_id and i.sku = p.sku
    join items r on r.org_id = p.org_id and r.sku = p.rec_sku
    join baskets b on b.org_id = p.org_id
),
ranked as (
    select rules.*,
           row_number() over (partition by org_id, sku order by confidence * lift desc, rec_sku) as rec_rank
    from rules
)
select
    k.org_id as org_id, k.sku as sku, k.rec_sku as rec_sku, c.product_name as rec_product_name,
    c.category as rec_category, c.list_price_cents as rec_price_cents, k.together as together,
    k.sku_baskets as sku_baskets, k.confidence as confidence, k.lift as lift, k.rec_rank as rec_rank
from ranked k
join {{ ref('dim_product') }} c on c.org_id = k.org_id and c.sku = k.rec_sku and c.is_current and c.retired is null
where k.rec_rank <= 8
{% endif %}

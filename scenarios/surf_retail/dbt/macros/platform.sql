{# Engine-portable building blocks (adapter.dispatch): the same models run on Postgres (core
   tier) and ClickHouse (analytics tier). Silver logic runs on Postgres only — the ClickHouse
   target copies Postgres' silver (see `silver_source`) — so only gold needs these. #}

{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}

{% macro week_start(col) %}{{ return(adapter.dispatch('week_start')(col)) }}{% endmacro %}
{% macro default__week_start(col) %}cast(date_trunc('week', {{ col }}) as date){% endmacro %}
{% macro clickhouse__week_start(col) %}toMonday({{ col }}){% endmacro %}

{% macro hour_start(col) %}{{ return(adapter.dispatch('hour_start')(col)) }}{% endmacro %}
{% macro default__hour_start(col) %}date_trunc('hour', {{ col }}){% endmacro %}
{% macro clickhouse__hour_start(col) %}toStartOfHour({{ col }}){% endmacro %}

{% macro hour_of_day(col) %}{{ return(adapter.dispatch('hour_of_day')(col)) }}{% endmacro %}
{% macro default__hour_of_day(col) %}cast(extract(hour from {{ col }}) as integer){% endmacro %}
{% macro clickhouse__hour_of_day(col) %}toHour({{ col }}){% endmacro %}

{% macro add_days(col, n) %}{{ return(adapter.dispatch('add_days')(col, n)) }}{% endmacro %}
{% macro default__add_days(col, n) %}cast({{ col }} + {{ n }} as date){% endmacro %}
{% macro clickhouse__add_days(col, n) %}addDays({{ col }}, {{ n }}){% endmacro %}

{% macro int_div(a, b) %}{{ return(adapter.dispatch('int_div')(a, b)) }}{% endmacro %}
{% macro default__int_div(a, b) %}({{ a }}) / nullif({{ b }}, 0){% endmacro %}
{% macro clickhouse__int_div(a, b) %}intDiv({{ a }}, nullIf({{ b }}, 0)){% endmacro %}

{# HMAC-SHA256 with the platform's key, which never appears in compiled SQL or dbt logs:
   data-transform installs `data_platform_private.pseudonymise(text)` (key in a private
   table) before every run. #}
{% macro pseudonymise(col) %}data_platform_private.pseudonymise({{ col }}){% endmacro %}

{# In the ClickHouse target a silver model is a copy of the Postgres silver table (read
   through ClickHouse's PostgreSQL database engine); its Postgres body is skipped. Silver
   models gold never reads (staged receipts, customers) are
   disabled there: only what the marts need is copied. #}
{% macro silver_source() %}
  {{ return(target.type == 'clickhouse') }}
{% endmacro %}

{# Incremental watermark with a lookback: data-ingest stamps received_at at transaction
   start, so a batch committing a moment after this run read a later row would otherwise be
   skipped forever. Reprocessing the overlap is idempotent (delete+insert by key). #}
{% macro received_since(col) %}
  {{ col }} > (select coalesce(max(_received_at), timestamptz '1900-01-01 00:00:00+00') - interval '10 minutes'
               from {{ this }})
{% endmacro %}

{% macro days_between(later, earlier) %}{{ return(adapter.dispatch('days_between')(later, earlier)) }}{% endmacro %}
{% macro default__days_between(later, earlier) %}cast(({{ later }}) - ({{ earlier }}) as integer){% endmacro %}
{% macro clickhouse__days_between(later, earlier) %}dateDiff('day', {{ earlier }}, {{ later }}){% endmacro %}

{% macro month_of(col) %}{{ return(adapter.dispatch('month_of')(col)) }}{% endmacro %}
{% macro default__month_of(col) %}cast(extract(month from {{ col }}) as integer){% endmacro %}
{% macro clickhouse__month_of(col) %}toMonth({{ col }}){% endmacro %}

{% macro week_of_year(col) %}{{ return(adapter.dispatch('week_of_year')(col)) }}{% endmacro %}
{% macro default__week_of_year(col) %}cast(extract(week from {{ col }}) as integer){% endmacro %}
{% macro clickhouse__week_of_year(col) %}toISOWeek({{ col }}){% endmacro %}

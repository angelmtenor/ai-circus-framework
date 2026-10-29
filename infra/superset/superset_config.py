"""Superset configuration for the Data Platform's bi tier — every secret from the environment.

Mirrors the ConfigMap in k8s/data-platform/bi/superset.yaml (keep the two in sync by hand).
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/superset"
)
_CACHE_URL = os.environ.get("SUPERSET_CACHE_URL", "redis://valkey:6379/3")
CACHE_CONFIG = {"CACHE_TYPE": "RedisCache", "CACHE_DEFAULT_TIMEOUT": 300, "CACHE_KEY_PREFIX": "superset_",
                "CACHE_REDIS_URL": _CACHE_URL}
DATA_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_KEY_PREFIX": "superset_data_", "CACHE_DEFAULT_TIMEOUT": 60}
FEATURE_FLAGS = {"DASHBOARD_RBAC": True, "ALERT_REPORTS": False}
FAB_ADD_SECURITY_API = True  # roles/users through the REST API (the bi-sync Job)
ROW_LIMIT = 10_000
SQL_MAX_ROW = 10_000
TALISMAN_ENABLED = False  # served over plain http behind Traefik on a laptop
WTF_CSRF_ENABLED = True
SUPERSET_WEBSERVER_TIMEOUT = 60

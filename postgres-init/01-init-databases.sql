-- Shared Postgres instance used by eval-service (eval_db) and mlflow (mlflow_db).
-- POSTGRES_USER=aiops is already created as superuser by the official postgres
-- image's entrypoint (via POSTGRES_USER/POSTGRES_PASSWORD env vars); this script
-- only needs to create the two extra databases that instance is expected to hold.
--
-- Deliberately NOT used by Langfuse -- Langfuse gets its own dedicated
-- `langfuse-db` Postgres container (see services/langfuse/compose.fragment.yml)
-- so a bad Langfuse migration can never touch this data, and vice versa.
CREATE DATABASE eval_db;
CREATE DATABASE mlflow_db;
